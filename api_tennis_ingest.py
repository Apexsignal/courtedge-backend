"""
api_tennis_ingest.py — převede api-tennis.com fixtures na `data_ingest.RawMatch`,
ať appka může použít STEJNÝ Elo/ace-rate engine (`build_player_ratings`) jako
u CSV importu (`data_ingest.py`), bez duplikace logiky.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import requests

import api_tennis_provider as provider
from data_ingest import PlayerRecord, RawMatch, build_player_ratings

# api-tennis.com appce vrací chybu "Maximum date range for odds is 7 days"
# na cokoliv širší — appka to zjistila živě (appka si dřív myslela, že jde
# o 30denní okna, appka tenhle limit objevila až při reálném bulk importu).
# Appka bere 7 jako bezpečnou horní mez pro `date_start`..`date_stop` VČETNĚ
# obou konců.
CHUNK_DAYS = 7
REQUEST_DELAY_SECONDS = 0.3  # appka mezi requesty čeká, ať nenarazí na rate limit při stovkách volání za sebou

# api-tennis.com nedává best_of přímo — appka to dřív měla natvrdo
# `best_of=3` VŽDY, i pro Grand Slamy. Appka to odhalila v backtestu:
# povrch "tráva" ukázal podezřele vysoký průměr gemů, protože appka do
# koše "bo3 tráva" počítala i Wimbledonské bo5 zápasy. WTA hraje bo5
# nikdy (i na Grand Slamech), takže appka bo5 detekuje jen pro ATP
# podle jména turnaje.
GRAND_SLAM_NAMES = {"australian open", "roland garros", "french open", "wimbledon", "us open"}


def _infer_best_of(tour: str, tourney_name: Optional[str]) -> int:
    if tour == "atp" and tourney_name and tourney_name.strip().lower() in GRAND_SLAM_NAMES:
        return 5
    return 3


def fixture_to_raw_match(fixture: dict, surface_by_tournament: dict[int, Optional[str]], tour: str) -> Optional[RawMatch]:
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

    # api-tennis appce nedává úroveň turnaje (Grand Slam/Masters/...)
    # přímo, ale appka od 2026-08-20 pozná aspoň Challenger podle
    # `event_type_type` ("Challenger Men/Women Singles") — appka mu dá
    # tourney_level='C', ať appce elo_model.py použije nižší K-faktor
    # (viz K_FACTOR_BY_LEVEL). Zbytek (Grand Slam/Masters/Tour) appka
    # pořád nerozlišuje, jede na defaultu.
    event_type_type = (fixture.get("event_type_type") or "").lower()
    tourney_level = "C" if "challenger" in event_type_type else None

    return RawMatch(
        tourney_date=tourney_date,
        surface=surface_by_tournament.get(fixture.get("tournament_key")),
        tourney_level=tourney_level,
        best_of=_infer_best_of(tour, fixture.get("tournament_name")),
        winner_id=str(winner_key),
        winner_name=winner_name,
        loser_id=str(loser_key),
        loser_name=loser_name,
        score=score,
        w_ace=provider.match_stat_value_int(statistics, winner_key, "Aces"),
        l_ace=provider.match_stat_value_int(statistics, loser_key, "Aces"),
        w_svgms=provider.match_stat_total(statistics, winner_key, "Service games won"),
        l_svgms=provider.match_stat_total(statistics, loser_key, "Service games won"),
        # "Total games won".stat_total appka dostává STEJNÉ pro oba hráče
        # (celkový počet gemů v zápase) — appka bere kterýkoliv z obou,
        # stejný princip jako u settlementu (api_tennis_sync.py).
        total_games=(
            provider.match_stat_total(statistics, winner_key, "Total games won")
            or provider.match_stat_total(statistics, loser_key, "Total games won")
        ),
    )


def fetch_raw_matches(
    tour: str,
    date_start: date,
    date_stop: date,
    on_progress: Optional[Callable[[date, date, int], None]] = None,
) -> list[RawMatch]:
    """Appka stáhne fixtures po 7denních oknech (viz CHUNK_DAYS — api-tennis.com
    delší rozsah v jednom volání odmítne) a spáruje je s povrchem přes
    `get_surface_by_tournament` (appka ho stahuje jednou za celé volání,
    ne pro každé okno zvlášť). `on_progress(chunk_start, chunk_stop,
    matches_so_far)` appka zavolá po každém okně — u víceletého importu
    appka udělá stovky requestů, appka chce mít jak appka postupuje."""
    surface_by_tournament = provider.get_surface_by_tournament(tour)

    raw_matches: list[RawMatch] = []
    cursor = date_start
    while cursor <= date_stop:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), date_stop)
        fixtures = _get_fixtures_with_retry(cursor.isoformat(), chunk_end.isoformat(), tour)
        for fixture in fixtures:
            raw_match = fixture_to_raw_match(fixture, surface_by_tournament, tour)
            if raw_match is not None:
                raw_matches.append(raw_match)
        if on_progress is not None:
            on_progress(cursor, chunk_end, len(raw_matches))
        time.sleep(REQUEST_DELAY_SECONDS)
        cursor = chunk_end + timedelta(days=1)

    raw_matches.sort(key=lambda m: m.tourney_date)
    return raw_matches


def _get_fixtures_with_retry(date_start: str, date_stop: str, tour: str, max_attempts: int = 4) -> list[dict]:
    """Appka jeden neúspěšný pokus (síť/rate limit) zkusí zopakovat s
    exponenciálním čekáním, než celý víceletý import kvůli jednomu
    zaseknutému oknu spadne."""
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            return provider.get_fixtures(date_start, date_stop, tour)
        except (requests.RequestException, RuntimeError):
            if attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    return []


def ingest_from_api_tennis(tour: str, date_start: date, date_stop: date, as_of: Optional[date] = None) -> list[PlayerRecord]:
    """Appčin vstupní bod pro `POST /admin/ingest-api-tennis` (viz backend_api.py)."""
    raw_matches = fetch_raw_matches(tour, date_start, date_stop)
    return build_player_ratings(raw_matches, tour=tour, as_of=as_of)
