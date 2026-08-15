"""
api_tennis_sync.py — appka tady používá api-tennis.com jako JEDINÝ zdroj
pro nadcházející zápasy, kurzy I settlement — na rozdíl od dřívějšího
plánu (the-odds-api pro kurzy + appka domýšlí settlement zvlášť), appka
zjistila, že api-tennis.com nabízí OBOJÍ pod STEJNÝM `event_key`/
`player_key`/`tournament_key` — appka se tak vyhne křehkému párování
podle jmen hráčů mezi dvěma různými API (`odds_provider.py`/`odds_sync.py`
appka nechává v repu jako záložní/druhý zdroj kurzů, ne jako primární
cestu).

`get_odds` appce dává ceny od ~12 bookmakerů najednou — appka bere
NEJLEPŠÍ dostupnou cenu napříč nimi (best price), ne první popadnutou.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import api_tennis_provider as provider
import db

ODDS_MARKET_WINNER = "Home/Away"
ODDS_MARKET_GAMES = "Over/Under by Games in Match"

# api-tennis.com nikdy nepotvrdila, v jaké časové zóně appka dostává
# `event_time` (README to mělo jako otevřenou otázku). Appka to
# 2026-08-15 ověřila na dvou reálných zápasech, co uživatel poslal
# jako screenshot z Tipsportu — appčin zobrazený čas vycházel o
# skoro 2,5 hodiny napřed. Příčina: appka `event_time` dřív brala
# jako UTC a tak ji uložila, ale na výstupu ještě přičítala +2h na
# pražský čas (viz ticket_telegram.py, `_kickoff_local`) — appka tím
# posouvala čas dvakrát. Skutečnost je, že `event_time` appka dostává
# už ve středoevropském čase, ne v UTC. Appka ji teď před uložením
# lokalizuje na Europe/Prague a uloží jako skutečné UTC.
API_TENNIS_SOURCE_TZ = ZoneInfo("Europe/Prague")


def _best_price(bookmaker_prices: dict) -> Optional[float]:
    prices = [float(p) for p in bookmaker_prices.values() if p]
    return max(prices) if prices else None


def _parse_start_time(event_date: str, event_time: str):
    try:
        naive = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=API_TENNIS_SOURCE_TZ).astimezone(timezone.utc)


def sync_upcoming_matches(tour: str, days_ahead: int = 7) -> dict[str, int]:
    """Appka natáhne fixtures na příštích `days_ahead` dní, uloží
    zápasy (appka hráče doplní přes `ensure_player_stub`, i když pro ně
    ještě nemá spočítaný rating) a k nim kurzy na výherce + gemy."""
    summary = {"fixtures_seen": 0, "matches_synced": 0, "odds_saved": 0}
    today = date.today()
    date_start, date_stop = today.isoformat(), (today + timedelta(days=days_ahead)).isoformat()

    fixtures = provider.get_fixtures(date_start, date_stop, tour)
    surface_by_tournament = provider.get_surface_by_tournament(tour)
    odds_by_event = provider.get_odds(date_start, date_stop, tour)

    for fixture in fixtures:
        summary["fixtures_seen"] += 1
        if fixture.get("event_status"):
            continue  # appka syncuje jen zápasy, co ještě NEzačaly (prázdný event_status)

        start_time = _parse_start_time(fixture.get("event_date", ""), fixture.get("event_time", ""))
        if start_time is None:
            continue

        player_a = db.ensure_player_stub(str(fixture["first_player_key"]), tour, fixture["event_first_player"])
        player_b = db.ensure_player_stub(str(fixture["second_player_key"]), tour, fixture["event_second_player"])

        match = db.upsert_upcoming_match({
            "external_id": str(fixture["event_key"]),
            "tour": tour,
            "tourney_name": fixture.get("tournament_name"),
            "surface": surface_by_tournament.get(fixture.get("tournament_key")),
            "tourney_level": None,
            "round": fixture.get("tournament_round"),
            "best_of": 3,
            "start_time": start_time,
            "player_a_id": player_a["id"],
            "player_b_id": player_b["id"],
        })
        summary["matches_synced"] += 1

        event_odds = odds_by_event.get(str(fixture["event_key"]), {})
        summary["odds_saved"] += _save_match_winner_odds(match["id"], event_odds)
        summary["odds_saved"] += _save_total_games_odds(match["id"], event_odds)

    return summary


def _save_match_winner_odds(match_id: int, event_odds: dict) -> int:
    market = event_odds.get(ODDS_MARKET_WINNER)
    if not market:
        return 0
    saved = 0
    home_price = _best_price(market.get("Home", {}))
    away_price = _best_price(market.get("Away", {}))
    if home_price:
        db.save_odds_snapshot(match_id, "match_winner", "player_a", None, "api-tennis-best", home_price)
        saved += 1
    if away_price:
        db.save_odds_snapshot(match_id, "match_winner", "player_b", None, "api-tennis-best", away_price)
        saved += 1
    return saved


def _save_total_games_odds(match_id: int, event_odds: dict) -> int:
    # Appka zjistila živě proti api-tennis.com, že tenhle trh je zanořený
    # o úroveň navíc: event_odds["Over/Under by Games in Match"] appka
    # dostane celý blok, a AŽ uvnitř něj appka najde "... Over"/"... Under"
    # podklíče (na rozdíl od `Home/Away`, kde appka má "Home"/"Away"
    # rovnou pod tržním klíčem, tady appka podklíč pojmenovaný stejně
    # dlouze jako rodič — appčino API vrací nekonzistentní zanoření
    # napříč trhy, ne appčina chyba v návrhu).
    games_market = event_odds.get(ODDS_MARKET_GAMES, {})
    over_prices = games_market.get(f"{ODDS_MARKET_GAMES} Over", {})
    under_prices = games_market.get(f"{ODDS_MARKET_GAMES} Under", {})
    saved = 0
    for line_str, bookmakers in over_prices.items():
        price = _best_price(bookmakers)
        if price:
            db.save_odds_snapshot(match_id, "total_games", "over", float(line_str), "api-tennis-best", price)
            saved += 1
    for line_str, bookmakers in under_prices.items():
        price = _best_price(bookmakers)
        if price:
            db.save_odds_snapshot(match_id, "total_games", "under", float(line_str), "api-tennis-best", price)
            saved += 1
    return saved


def settle_finished_matches(tour: str, date_start: date, date_stop: date) -> dict[str, int]:
    """Appka natáhne DOHRANÉ fixtures za dané období a zapíše výsledek
    (vítěz, total_games, total_aces, retirement) do appčiny `matches`
    tabulky — appka je páruje přes `external_id` (= api-tennis
    `event_key`, appka ho appce uložila při `sync_upcoming_matches`).
    Appka NEŘEŠÍ zápasy appka nikdy nesynchronizovala jako nadcházející
    (appka je zkrátka nemá v DB, `record_match_result` by neměl co
    updatovat) — appka to sama detekuje přes `db.get_pending_matches`
    z volajícího místa (viz backend_api.py `/admin/settle-all-pending`).
    """
    summary = {"finished_seen": 0, "results_recorded": 0}
    fixtures = provider.get_fixtures(date_start.isoformat(), date_stop.isoformat(), tour)

    for fixture in fixtures:
        if fixture.get("event_status") not in provider.FINISHED_STATUSES:
            continue
        summary["finished_seen"] += 1

        existing = db.get_match_by_external_id(str(fixture["event_key"]), tour)
        if existing is None:
            continue  # appka tenhle zápas nikdy nesledovala (nebyl v appčině upcoming syncu)

        winner_side = fixture.get("event_winner")
        if winner_side not in ("First Player", "Second Player"):
            continue

        winner_key = fixture["first_player_key"] if winner_side == "First Player" else fixture["second_player_key"]
        winner_player = db.get_player_by_external_id(str(winner_key), tour)
        if winner_player is None:
            continue

        statistics = fixture.get("statistics") or []
        # "Total games won".stat_total appka dostává STEJNÉ pro oba
        # hráče (je to celkový počet gemů v zápase, appka bere
        # kterýkoliv z obou jako combined_games, ne že by je sčítala).
        combined_games = (
            provider.match_stat_total(statistics, fixture["first_player_key"], "Total games won")
            or provider.match_stat_total(statistics, fixture["second_player_key"], "Total games won")
            or 0
        )

        total_aces = (
            provider.match_stat_value_int(statistics, fixture["first_player_key"], "Aces") or 0
        ) + (
            provider.match_stat_value_int(statistics, fixture["second_player_key"], "Aces") or 0
        )

        db.record_match_result(
            existing["id"], winner_player["id"], fixture.get("event_final_result", ""),
            combined_games, total_aces, fixture["event_status"] in provider.RETIREMENT_STATUSES,
        )
        summary["results_recorded"] += 1

    return summary
