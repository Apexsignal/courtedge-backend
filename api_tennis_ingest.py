"""
api_tennis_ingest.py — převede api-tennis.com fixtures na `data_ingest.RawMatch`,
ať appka může použít STEJNÝ Elo/ace-rate engine (`build_player_ratings`) jako
u CSV importu (`data_ingest.py`), bez duplikace logiky.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import api_tennis_provider as provider
from data_ingest import PlayerRecord, RawMatch, build_player_ratings

MONTH_CHUNK_DAYS = 30  # appka stahuje po měsíčních oknech, ať appka nenarazí na nezdokumentovaný limit velikosti odpovědi


def fixture_to_raw_match(fixture: dict, surface_by_tournament: dict[int, Optional[str]]) -> Optional[RawMatch]:
    """Appka vrátí None pro zápasy appka nemůže/nemá smysl zpracovat
    (neskončené, bez jasného vítěze, zrušené)."""
    status = fixture.get("event_status")
    if status not in provider.FINISHED_STATUSES:
        return None

    winner_side = fixture.get("event_winner")
    if winner_side not in ("First Player", "Second Player"):
        return None  # appka bez jasného vítěze zápas do ratingu nezahrne

    first_key = fixture.get("first_player_key")
    second_key = fixture.get("second_player_key")
    if not first_key or not second_key:
        return None

    is_winner_first = winner_side == "First Player"
    winner_key = first_key if is_winner_first else second_key
    loser_key = second_key if is_winner_first else first_key
    winner_name = fixture["event_first_player"] if is_winner_first else fixture["event_second_player"]
    loser_name = fixture["event_second_player"] if is_winner_first else fixture["event_first_player"]

    try:
        tourney_date = datetime.strptime(fixture["event_date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None

    statistics = fixture.get("statistics") or []
    is_retirement = status in provider.RETIREMENT_STATUSES
    score = (fixture.get("event_final_result") or "").strip()
    if is_retirement:
        score = f"{score} RET".strip()  # appka takhle appčino RawMatch.is_retirement detekuje stejně jako u CSV formátu

    return RawMatch(
        tourney_date=tourney_date,
        surface=surface_by_tournament.get(fixture.get("tournament_key")),
        tourney_level=None,  # api-tennis appce nedává úroveň turnaje (Grand Slam/Masters/...) přímo — appka K-faktor zatím jede na defaultu
        best_of=3,
        winner_id=str(winner_key),
        winner_name=winner_name,
        loser_id=str(loser_key),
        loser_name=loser_name,
        score=score,
        w_ace=provider.match_stat_value_int(statistics, winner_key, "Aces"),
        l_ace=provider.match_stat_value_int(statistics, loser_key, "Aces"),
        w_svgms=provider.match_stat_total(statistics, winner_key, "Service games won"),
        l_svgms=provider.match_stat_total(statistics, loser_key, "Service games won"),
    )


def fetch_raw_matches(tour: str, date_start: date, date_stop: date) -> list[RawMatch]:
    """Appka stáhne fixtures po měsíčních oknech (viz MONTH_CHUNK_DAYS)
    a spáruje je s povrchem přes `get_surface_by_tournament` (appka ho
    stahuje jednou za celé volání, ne pro každé okno zvlášť)."""
    surface_by_tournament = provider.get_surface_by_tournament(tour)

    raw_matches: list[RawMatch] = []
    cursor = date_start
    while cursor <= date_stop:
        chunk_end = min(cursor + timedelta(days=MONTH_CHUNK_DAYS - 1), date_stop)
        fixtures = provider.get_fixtures(cursor.isoformat(), chunk_end.isoformat(), tour)
        for fixture in fixtures:
            raw_match = fixture_to_raw_match(fixture, surface_by_tournament)
            if raw_match is not None:
                raw_matches.append(raw_match)
        cursor = chunk_end + timedelta(days=1)

    raw_matches.sort(key=lambda m: m.tourney_date)
    return raw_matches


def ingest_from_api_tennis(tour: str, date_start: date, date_stop: date, as_of: Optional[date] = None) -> list[PlayerRecord]:
    """Appčin vstupní bod pro `POST /admin/ingest-api-tennis` (viz backend_api.py)."""
    raw_matches = fetch_raw_matches(tour, date_start, date_stop)
    return build_player_ratings(raw_matches, tour=tour, as_of=as_of)
