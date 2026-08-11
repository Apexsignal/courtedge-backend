#!/usr/bin/env python3
"""
scripts/backtest_calibration.py — appka tady ověřuje, jestli appčiny tři
modely (výherce, gemy, esa) fakt sedí na realitě, ne jen appka VĚŘÍ, že
by měly. Bez tohohle appka jen hádala, jak přesné (nebo nadhodnocené)
jsou appčiny nominální pravděpodobnosti (viz README, appka to zjistila,
když appčin games model dal 98 % jistotu na zápas, kde appčin win model
dal na ten samý zápas jen 71 %).

**Walk-forward, BEZ nakukování dopředu:** appka projde historické zápasy
CHRONOLOGICKY. U KAŽDÉHO appka nejdřív spočítá predikci z ratingu, jak
vypadal TĚSNĚ PŘED tímhle zápasem — teprve POTOM appka zápas použije na
update Elo/ace-rate accumulátorů pro zápasy, co přijdou dál. Appka první
`WARMUP_MONTHS` NEHODNOTÍ (appka na začátku nemá o hráčích žádný signál,
appka by tím jinak sama sobě podhodnotila kalibraci).

**Neutrální značení pro win-model kalibraci:** appka NEPOUŽÍVÁ "vítěz/
poražený" jako nálepku pro appčinu predikci (appka by tím zaručeně vždycky
"trefila" appku, protože appka by pořád predikovala pravděpodobnost TOHO,
o kom appka už ví, že vyhrál — to by kalibraci zbytečně nafouklo). appka
místo toho appku značí hráče A/B podle appka lexikograficky menšího ID
(nezávisí na výsledku) a appka sleduje, jestli P(A vyhraje) fakt sedí
s tím, jak často A doopravdy vyhrává v appka daném pásmu pravděpodobnosti.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_tennis_ingest
from data_ingest import PlayerAccumulator, RawMatch
from elo_model import EloEngine, win_probability
from market_models import estimate_total_aces, estimate_total_games

MIN_HISTORY_MATCHES = 10  # appka hodnotí predikci jen když OBA hráči mají za appka posledních 12 měsíců aspoň tolik zápasů (mirrors safety filter v ticket_builder.py)


def run_backtest(matches: list[RawMatch], score_from: date) -> dict:
    engine = EloEngine()
    accumulators: dict[str, PlayerAccumulator] = {}

    def get_acc(external_id: str, name: str) -> PlayerAccumulator:
        acc = accumulators.get(external_id)
        if acc is None:
            acc = PlayerAccumulator(external_id=external_id, full_name=name)
            accumulators[external_id] = acc
        return acc

    win_records: list[dict] = []
    games_records: list[dict] = []
    aces_records: list[dict] = []

    for m in matches:
        winner_acc = get_acc(m.winner_id, m.winner_name)
        loser_acc = get_acc(m.loser_id, m.loser_name)
        winner_rating = engine.get_or_create(m.winner_id)
        loser_rating = engine.get_or_create(m.loser_id)

        cutoff_12mo = m.tourney_date - timedelta(days=365)
        enough_history = (
            winner_acc.matches_played_since(cutoff_12mo) >= MIN_HISTORY_MATCHES
            and loser_acc.matches_played_since(cutoff_12mo) >= MIN_HISTORY_MATCHES
        )

        if m.tourney_date >= score_from and enough_history:
            surface = m.surface or "hard"

            # --- výherce: neutrální A/B značení, appka nepoužívá vítěz/poražený ---
            if m.winner_id < m.loser_id:
                a_rating, b_rating, a_won = winner_rating, loser_rating, True
            else:
                a_rating, b_rating, a_won = loser_rating, winner_rating, False
            p_a = win_probability(a_rating, b_rating, surface)
            win_records.append({"date": m.tourney_date.isoformat(), "p_a": p_a, "a_won": a_won})

            # --- gemy ---
            games_est = None
            if m.total_games is not None and m.surface:
                games_est = estimate_total_games(
                    winner_rating.blended_elo(m.surface), loser_rating.blended_elo(m.surface), m.surface, m.best_of,
                )
                elo_gap = abs(winner_rating.blended_elo(m.surface) - loser_rating.blended_elo(m.surface))
                games_records.append({
                    "date": m.tourney_date.isoformat(), "expected_games": games_est.expected_games,
                    "actual_games": m.total_games, "elo_gap": elo_gap, "surface": m.surface,
                })

            # --- esa ---
            if m.w_ace is not None and m.l_ace is not None and m.surface:
                w_rate = winner_acc.ace_rate(m.surface)
                l_rate = loser_acc.ace_rate(m.surface)
                if w_rate is not None and l_rate is not None:
                    if games_est is None:
                        games_est = estimate_total_games(
                            winner_rating.blended_elo(m.surface), loser_rating.blended_elo(m.surface), m.surface, m.best_of,
                        )
                    aces_est = estimate_total_aces(w_rate, l_rate, games_est)
                    aces_records.append({
                        "date": m.tourney_date.isoformat(), "expected_aces": aces_est.expected_aces,
                        "actual_aces": m.w_ace + m.l_ace, "surface": m.surface,
                    })

        # appka teprve TEĎ, PO scorování, zápas použije na update stavu pro příští zápasy
        winner_acc.record_match(m, is_winner=True, retired=False)
        loser_acc.record_match(m, is_winner=False, retired=m.is_retirement)
        engine.process_match(m.winner_id, m.loser_id, m.surface, m.tourney_level)

    return {"win": win_records, "games": games_records, "aces": aces_records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tour", required=True, choices=["atp", "wta"])
    parser.add_argument("--fetch-start", required=True, help="YYYY-MM-DD — appka odsud začne stahovat a zahřívat rating")
    parser.add_argument("--score-from", required=True, help="YYYY-MM-DD — appka odsud teprve HODNOTÍ predikce (warmup období appka jen zahřeje rating)")
    parser.add_argument("--fetch-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="cesta k výstupnímu JSON se syrovými (predikce, realita) páry")
    args = parser.parse_args()

    def progress(chunk_start, chunk_end, matches_so_far):
        print(f"[{args.tour}] {chunk_start}..{chunk_end}: {matches_so_far} zápasů zatím", file=sys.stderr, flush=True)

    raw_matches = api_tennis_ingest.fetch_raw_matches(
        args.tour, date.fromisoformat(args.fetch_start), date.fromisoformat(args.fetch_end), on_progress=progress,
    )
    print(f"[{args.tour}] celkem staženo {len(raw_matches)} zápasů", file=sys.stderr)

    result = run_backtest(raw_matches, score_from=date.fromisoformat(args.score_from))
    print(f"[{args.tour}] ohodnoceno: {len(result['win'])} win, {len(result['games'])} games, {len(result['aces'])} aces záznamů", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"tour": args.tour, **result}, f)

    print(f"[{args.tour}] hotovo, zapsáno do {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
