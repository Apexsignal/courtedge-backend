"""
data_ingest.py — import historických zápasů (CSV ve formátu Sackmann/TML)
a přepočet Elo ratingů + ace rate + bezpečnostních čítačů za všechny hráče.

**DŮLEŽITÉ — appka záměrně NEBALÍ žádná historická data do repozitáře.**
Originální zdroj `github.com/JeffSackmann/tennis_atp` už na GitHubu
neexistuje (ověřeno — repo vrací 404, stejně jako `tennis_wta`). Appka
při hledání náhrady našla `github.com/Tennismylife/TML-Database`
(stejný sloupcový formát, aktivně udržovaný, 1968–dnes), ALE ten je
licencovaný **Creative Commons Non-Commercial Share Alike** a jeho
README výslovně říká: "Redistribution, commercial use, or selling of
the raw database without permission... may violate copyright." CourtEdge
je komerční produkt (platené předplatné) — appka NESMÍ na tenhle zdroj
tiše navázat produkci, dokud tohle uživatel nevyřeší (svolení od
TennisMyLife/ATP, nebo přechod na licencovaný placený zdroj). Viz
README.md, sekce "Zdroj historických dat — NEVYŘEŠENO".

Tenhle modul je proto navržený zdroj-agnosticky: čte CSV soubory ve
známém formátu (stejné sloupce jako Sackmann i TML) z LOKÁLNÍ cesty
(`HISTORICAL_DATA_SOURCE`), appka sama nic nestahuje ani neclonuje.

Formát vstupních CSV (jeden řádek = jeden zápas, chronologicky v rámci
souboru; appka řadí přes tourney_date + match_num napříč soubory):
tourney_id, tourney_name, surface, draw_size, tourney_level, tourney_date,
match_num, winner_id, winner_name, winner_hand, winner_ioc, loser_id,
loser_name, loser_hand, loser_ioc, score, best_of, round, w_ace, l_ace,
w_SvGms, l_SvGms, ...
"""
from __future__ import annotations

import csv
import glob
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

from elo_model import EloEngine

RETIREMENT_MARKERS = ("RET", "W/O", "WO", "DEF")
SURFACE_MAP = {"Hard": "hard", "Clay": "clay", "Grass": "grass", "Carpet": "carpet"}
RECENT_WINDOW_DAYS = 365


@dataclass
class RawMatch:
    tourney_date: date
    surface: Optional[str]
    tourney_level: Optional[str]
    best_of: int
    winner_id: str
    winner_name: str
    loser_id: str
    loser_name: str
    score: str
    w_ace: Optional[int]
    l_ace: Optional[int]
    w_svgms: Optional[int]
    l_svgms: Optional[int]
    total_games: Optional[int] = None  # appka to potřebuje jen pro zpětný test (backtest_calibration.py) — CSV cesta ho nechává None, appka ho tam nepotřebuje

    @property
    def is_retirement(self) -> bool:
        upper_score = (self.score or "").upper()
        return any(marker in upper_score for marker in RETIREMENT_MARKERS)


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def read_matches_csv(path: str) -> Iterator[RawMatch]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tourney_date = _parse_date(row.get("tourney_date", ""))
            if tourney_date is None:
                continue
            winner_id = (row.get("winner_id") or row.get("winner_name") or "").strip()
            loser_id = (row.get("loser_id") or row.get("loser_name") or "").strip()
            if not winner_id or not loser_id:
                continue
            yield RawMatch(
                tourney_date=tourney_date,
                surface=SURFACE_MAP.get((row.get("surface") or "").strip()),
                tourney_level=(row.get("tourney_level") or "").strip() or None,
                best_of=_parse_int(row.get("best_of", "")) or 3,
                winner_id=winner_id,
                winner_name=(row.get("winner_name") or "").strip(),
                loser_id=loser_id,
                loser_name=(row.get("loser_name") or "").strip(),
                score=(row.get("score") or "").strip(),
                w_ace=_parse_int(row.get("w_ace", "")),
                l_ace=_parse_int(row.get("l_ace", "")),
                w_svgms=_parse_int(row.get("w_SvGms", "")),
                l_svgms=_parse_int(row.get("l_SvGms", "")),
            )


def read_matches_dir(directory: str) -> list[RawMatch]:
    """Appka přečte a chronologicky seřadí VŠECHNY CSV v adresáři (typicky
    jeden soubor na sezónu, jak je zvykem u Sackmann/TML formátu)."""
    matches: list[RawMatch] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        matches.extend(read_matches_csv(path))
    matches.sort(key=lambda m: m.tourney_date)
    return matches


@dataclass
class PlayerAccumulator:
    external_id: str
    full_name: str = ""
    match_dates: list[date] = field(default_factory=list)
    retirement_dates: list[date] = field(default_factory=list)
    aces_by_surface: dict[str, int] = field(default_factory=dict)
    service_games_by_surface: dict[str, int] = field(default_factory=dict)
    last_match_date: Optional[date] = None

    def record_match(self, m: RawMatch, is_winner: bool, retired: bool) -> None:
        self.match_dates.append(m.tourney_date)
        self.last_match_date = m.tourney_date
        if retired:
            self.retirement_dates.append(m.tourney_date)
        if m.surface:
            aces = m.w_ace if is_winner else m.l_ace
            svgms = m.w_svgms if is_winner else m.l_svgms
            if aces is not None and svgms:
                self.aces_by_surface[m.surface] = self.aces_by_surface.get(m.surface, 0) + aces
                self.service_games_by_surface[m.surface] = self.service_games_by_surface.get(m.surface, 0) + svgms

    def ace_rate(self, surface: str) -> Optional[float]:
        games = self.service_games_by_surface.get(surface, 0)
        if games <= 0:
            return None
        return round(self.aces_by_surface.get(surface, 0) / games, 4)

    def matches_played_since(self, cutoff: date) -> int:
        return sum(1 for d in self.match_dates if d >= cutoff)

    def retirements_since(self, cutoff: date) -> int:
        return sum(1 for d in self.retirement_dates if d >= cutoff)


@dataclass
class PlayerRecord:
    external_id: str
    tour: str
    full_name: str
    elo_overall: float
    elo_hard: float
    elo_clay: float
    elo_grass: float
    elo_carpet: float
    matches_played_total: int
    matches_played_12mo: int
    ace_rate_hard: Optional[float]
    ace_rate_clay: Optional[float]
    ace_rate_grass: Optional[float]
    ace_rate_carpet: Optional[float]
    recent_retirements_12mo: int
    last_match_date: Optional[date]


def build_player_ratings(matches: list[RawMatch], tour: str, as_of: Optional[date] = None) -> list[PlayerRecord]:
    """
    Appka projede zápasy CHRONOLOGICKY jednou (Elo je stavový model,
    pořadí je závazné) a zároveň akumuluje ace rate / matches-played /
    retirement čítače na hráče. `as_of` appka bere jako "dnešek" pro
    12měsíční okna — default je datum posledního zápasu v datasetu.
    """
    engine = EloEngine()
    accumulators: dict[str, PlayerAccumulator] = {}

    def get_acc(external_id: str, name: str) -> PlayerAccumulator:
        acc = accumulators.get(external_id)
        if acc is None:
            acc = PlayerAccumulator(external_id=external_id, full_name=name)
            accumulators[external_id] = acc
        elif name:
            acc.full_name = name
        return acc

    latest_date: Optional[date] = None
    for m in matches:
        latest_date = m.tourney_date if latest_date is None else max(latest_date, m.tourney_date)
        winner_acc = get_acc(m.winner_id, m.winner_name)
        loser_acc = get_acc(m.loser_id, m.loser_name)
        retired = m.is_retirement
        winner_acc.record_match(m, is_winner=True, retired=False)
        loser_acc.record_match(m, is_winner=False, retired=retired)
        # Appka aktualizuje Elo VŽDY, i když appka nezná povrch (chybějící
        # `surface` appka dřív používala jako podmínku pro celé volání, což
        # zbytečně přeskočilo i overall rating, který na povrchu nezávisí —
        # update_ratings/EloEngine povrch bez problému zvládne jako None,
        # jen surface-specific rating se neaktualizuje).
        engine.process_match(m.winner_id, m.loser_id, m.surface, m.tourney_level)

    cutoff = (as_of or latest_date or date.today()) - timedelta(days=RECENT_WINDOW_DAYS)
    ratings = engine.all_ratings()

    records: list[PlayerRecord] = []
    for external_id, acc in accumulators.items():
        rating = ratings.get(external_id)
        records.append(
            PlayerRecord(
                external_id=external_id,
                tour=tour,
                full_name=acc.full_name,
                elo_overall=rating.elo_overall if rating else 1500.0,
                elo_hard=rating.elo_hard if rating else 1500.0,
                elo_clay=rating.elo_clay if rating else 1500.0,
                elo_grass=rating.elo_grass if rating else 1500.0,
                elo_carpet=rating.elo_carpet if rating else 1500.0,
                matches_played_total=len(acc.match_dates),
                matches_played_12mo=acc.matches_played_since(cutoff),
                ace_rate_hard=acc.ace_rate("hard"),
                ace_rate_clay=acc.ace_rate("clay"),
                ace_rate_grass=acc.ace_rate("grass"),
                ace_rate_carpet=acc.ace_rate("carpet"),
                recent_retirements_12mo=acc.retirements_since(cutoff),
                last_match_date=acc.last_match_date,
            )
        )
    return records


def ingest_from_source(source_dir: str, tour: str, as_of: Optional[date] = None) -> list[PlayerRecord]:
    """Vstupní bod appky pro `POST /admin/ingest-historical-data` (viz backend_api.py).
    `tour` appka volí podle toho, jakou složku (ATP/WTA data) ingestuje —
    appka drží ATP a WTA ratingy odděleně, i kdyby dva hráči náhodou sdíleli external_id napříč zdroji."""
    if not source_dir or not os.path.isdir(source_dir):
        raise RuntimeError(
            f"HISTORICAL_DATA_SOURCE '{source_dir}' neexistuje nebo není nastavená. "
            "Appka záměrně nebalí historická data do repa (viz licenční poznámka "
            "nahoře v tomhle souboru) — je potřeba je appce dodat zvenčí."
        )
    matches = read_matches_dir(source_dir)
    return build_player_ratings(matches, tour=tour, as_of=as_of)
