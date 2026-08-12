"""
ticket_generation.py — appka tady spojuje elo_model + market_models +
ticket_builder + db do jednoho volání: "z aktuálních nadcházejících
zápasů postav jeden tiket". Odděleno od backend_api.py, ať appka
endpointy drží tenké a tuhle logiku jde jednou otestovat izolovaně.
"""
from __future__ import annotations

from typing import Optional

import db
import head_to_head
from elo_model import PlayerRating, combined_confidence, win_probability
from market_models import estimate_total_aces, estimate_total_games
from ticket_builder import (
    Candidate,
    MarketThreshold,
    SafetyContext,
    build_favorites_ticket,
    passes_safety_filters,
)

DEFAULT_ACES_LINE = 20.5  # appka nemá tržní kurz na esa (viz odds_provider.py) — startovní hranice, dokud appka nemá lepší zdroj


def _to_float(value) -> Optional[float]:
    """psycopg2 appce vrací NUMERIC sloupce jako decimal.Decimal, ne float
    — market_models.py počítá s obyčejným float aritmetikou, appka to tu
    sjednocuje na hranici DB/výpočtu, ne na desítkách míst zvlášť."""
    return float(value) if value is not None else None


def _fetch_form_safe(m: dict) -> Optional[head_to_head.MatchupForm]:
    """Appka appce zavolá get_H2H (viz head_to_head.py) — appka to
    obaluje try/except, protože appka radši dostane tiket BEZ H2H/únava
    signálu, než aby appce jeden nedostupný zápas na api-tennis.com
    (výpadek, chybějící player_key, rate limit) shodil celé generování
    tiketu."""
    try:
        return head_to_head.fetch_matchup_form(
            m["a_external_id"], m["b_external_id"], m["start_time"].date(),
        )
    except Exception:
        return None


def _rating_from_row(row: dict, prefix: str) -> PlayerRating:
    return PlayerRating(
        player_id=0,
        elo_overall=float(row[f"{prefix}_elo_overall"]),
        elo_hard=float(row[f"{prefix}_elo_hard"]),
        elo_clay=float(row[f"{prefix}_elo_clay"]),
        elo_grass=float(row[f"{prefix}_elo_grass"]),
        elo_carpet=float(row[f"{prefix}_elo_carpet"]),
        matches_played_total=row.get(f"{prefix}_matches_played_total") or 0,
    )


def build_candidates_from_pending_matches() -> tuple[list[Candidate], dict[int, dict]]:
    """Appka vrátí (kandidáti, match_id -> match metadata) — metadata appka
    potřebuje pro render/uložení tiketu (jména hráčů, info o turnaji)."""
    thresholds_raw = db.get_market_thresholds()
    thresholds = {
        code: MarketThreshold(
            market_code=code,
            min_confidence=float(t["min_confidence"]),
            min_matches_played_12mo=t["min_matches_played_12mo"],
        )
        for code, t in thresholds_raw.items()
    }

    matches = db.get_pending_matches()
    candidates: list[Candidate] = []
    match_meta: dict[int, dict] = {}

    for m in matches:
        surface = m.get("surface") or "hard"
        rating_a = _rating_from_row(m, "a")
        rating_b = _rating_from_row(m, "b")
        match_meta[m["id"]] = m

        safety_ctx = SafetyContext(
            match_id=m["id"],
            player_a_matches_played_12mo=m["a_matches_played_12mo"],
            player_b_matches_played_12mo=m["b_matches_played_12mo"],
            player_a_recent_retirements=m["a_recent_retirements"],
            player_b_recent_retirements=m["b_recent_retirements"],
        )

        # Appka spočítá H2H a únava/odpočinek signál jednou na zápas (viz
        # head_to_head.py) — appka ho použije jak pro pravděpodobnost
        # výherce (H2H poměr + únavový Elo posun), tak pro appčinu
        # důvěru u všech tří trhů (dlouhá pauza appku sníží).
        form = _fetch_form_safe(m)
        fatigue_adj = head_to_head.fatigue_elo_adjustment(form) if form else 0.0
        layoff_mult = head_to_head.layoff_confidence_multiplier(form) if form else 1.0

        # Appka spočítá nejistotu jednou na zápas — stejná pro všechny tři
        # trhy, protože vychází ze stejné dvojice hráčů (viz elo_model.py).
        confidence = combined_confidence(rating_a, rating_b) * layoff_mult

        # --- trh 1: výherce zápasu ---
        odds_winner = {o["selection"]: float(o["odds_decimal"]) for o in db.get_latest_odds(m["id"], "match_winner")}
        prob_a = win_probability(rating_a, rating_b, surface, fatigue_elo_adjustment=fatigue_adj, confidence_multiplier=layoff_mult)
        if form:
            prob_a = head_to_head.h2h_adjusted_probability(prob_a, form)
        for selection, prob in (("player_a", prob_a), ("player_b", 1 - prob_a)):
            cand = Candidate(
                match_id=m["id"], market_code="match_winner", selection=selection, line=None,
                model_probability=prob, market_odds=odds_winner.get(selection),
            )
            if passes_safety_filters(cand, safety_ctx, thresholds):
                candidates.append(cand)

        # --- trh 2: over/under gemů ---
        games_est = estimate_total_games(
            rating_a.blended_elo(surface), rating_b.blended_elo(surface), surface, m.get("best_of") or 3,
            confidence=confidence,
        )
        odds_games: dict[tuple[str, float], float] = {}
        for o in db.get_latest_odds(m["id"], "total_games"):
            if o.get("line") is not None:
                odds_games[(o["selection"], float(o["line"]))] = float(o["odds_decimal"])
        for line in {ln for (_sel, ln) in odds_games.keys()}:
            for selection, prob_fn in (("over", games_est.prob_over), ("under", games_est.prob_under)):
                cand = Candidate(
                    match_id=m["id"], market_code="total_games", selection=selection, line=line,
                    model_probability=prob_fn(line), market_odds=odds_games.get((selection, line)),
                )
                if passes_safety_filters(cand, safety_ctx, thresholds):
                    candidates.append(cand)

        # --- trh 3: over/under es ---
        # the-odds-api nemá bookmaker trh na esa (viz odds_provider.py) —
        # appka pravděpodobnost i tak spočítá, ale market_odds zůstane
        # None, dokud appka nenajde zdroj kurzu → ticket_builder.build_ticket
        # takový kandidát nepoužije pro sestavení tiketu (potřebuje odds).
        aces_est = estimate_total_aces(
            _to_float(m.get(f"a_ace_rate_{surface}")), _to_float(m.get(f"b_ace_rate_{surface}")), games_est,
            confidence=confidence,
        )
        for selection, prob_fn in (("over", aces_est.prob_over), ("under", aces_est.prob_under)):
            cand = Candidate(
                match_id=m["id"], market_code="total_aces", selection=selection, line=DEFAULT_ACES_LINE,
                model_probability=prob_fn(DEFAULT_ACES_LINE), market_odds=None,
            )
            if passes_safety_filters(cand, safety_ctx, thresholds):
                candidates.append(cand)

    return candidates, match_meta


def _save_favorites_ticket(built, match_meta: dict[int, dict], user_id: Optional[int]) -> dict:
    """Appka uloží appka postavený tiket (BuiltTicket) do DB a obohatí
    legy appka jmény hráčů pro render/odeslání."""
    legs_for_db = []
    legs_for_render = []
    for leg in built.legs:
        m = match_meta[leg.match_id]
        base = {
            "match_id": leg.match_id, "market_code": leg.market_code, "selection": leg.selection,
            "line": leg.line, "model_probability": leg.model_probability, "market_odds": leg.market_odds,
        }
        legs_for_db.append(base)
        legs_for_render.append({
            **base,
            "player_a": m["player_a_name"], "player_b": m["player_b_name"],
            "tourney_name": m.get("tourney_name"), "start_time": m.get("start_time"),
        })

    ticket = db.save_ticket(user_id, built.total_odds, legs_for_db, ticket_type="favorites")
    ticket["legs"] = legs_for_render
    return ticket


def generate_daily_tickets(user_id: Optional[int] = None) -> dict[str, Optional[dict]]:
    """
    Appka appce vrátí {"favorites_1": tiket|None, "favorites_2": tiket|None}
    — DVA samostatné denní tikety, oba jen z trhu výherce zápasu
    (match_winner). Appka appku 2026-08-12 přesunula z gemů na favority
    na výslovné přání uživatele (viz README, "Favorité místo gemů") —
    appka živě zjistila, že appčin trh gemů má u skutečného bookmakera
    jinou hranici, než appka appce ukazuje. Byla to jiná sázka, ne jen
    jiná cena. Trh výherce zápasu žádnou hranici nemá, je to stejná
    sázka všude.

    Každý tiket appka staví přes ticket_builder.build_favorites_ticket
    — appka nechá jen kurz appka v pásmu 1,3-2,0 na leg a appka přidává
    favority podle jistoty, dokud appku kombinovaný kurz nepřesáhne 1,8.
    Druhý tiket appka staví ze ZBÝVAJÍCÍCH zápasů, ať appka dva denní
    tikety nikdy nesdílí stejný zápas.

    Appka kandidáty počítá JEDNOU (včetně H2H volání na api-tennis.com)
    a použije je pro OBA tikety.
    """
    candidates, match_meta = build_candidates_from_pending_matches()
    winner_candidates = [c for c in candidates if c.market_code == "match_winner"]

    built_1 = build_favorites_ticket(winner_candidates)
    used_match_ids = frozenset(leg.match_id for leg in built_1.legs) if built_1 else frozenset()
    built_2 = build_favorites_ticket(winner_candidates, exclude_match_ids=used_match_ids)

    return {
        "favorites_1": _save_favorites_ticket(built_1, match_meta, user_id) if built_1 else None,
        "favorites_2": _save_favorites_ticket(built_2, match_meta, user_id) if built_2 else None,
    }
