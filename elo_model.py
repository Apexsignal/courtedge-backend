"""
elo_model.py — surface-vážený Elo rating pro tenis.

Princip (obdoba fotbalového Dixon-Coles modelu, jen jednodušší — tenis
nemá remízy a jeden zápas = jeden vítěz, takže klasický Elo funguje
přímo bez úprav na "výsledek" jako u fotbalu):

1. Appka drží PĚT ratingů na hráče: jeden "overall" (napříč povrchy) a
   čtyři surface-specific (hard/clay/grass/carpet). Zápas na daném
   povrchu aktualizuje JAK overall, TAK příslušný surface rating —
   overall menší vahou (K_OVERALL < K_SURFACE), protože surface rating
   je specifičtější signál pro predikci na tom samém povrchu.
2. Pravděpodobnost výhry appka počítá ze SMĚSI overall a surface ratingu
   (SURFACE_WEIGHT), ne z jednoho z nich — u hráčů s málo zápasy na
   daném povrchu surface rating sám o sobě moc neříká, overall to
   vyrovnává.
3. K-faktor appka odstupňuje podle úrovně turnaje (Grand Slam > Masters
   > Tour > ostatní) — výhra na Grand Slamu je silnější signál než
   výhra na menším turnaji, stejně jako u fotbalových lig s různou
   vahou.

Appka NEPOČÍTÁ žádné "value" vs tržní kurz — jen vlastní pravděpodobnost.
Řazení kandidátů podle jistoty (ne edge) dělá ticket_builder.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

INITIAL_ELO = 1500.0

# K-faktor podle úrovně turnaje — appka zatím jede na hrubém odhadu,
# kalibrace na reálném backtestu je otevřená (viz README).
K_FACTOR_BY_LEVEL = {
    "G": 40,   # Grand Slam
    "M": 32,   # Masters 1000 / WTA 1000
    "F": 36,   # Tour Finals
    "A": 28,   # běžný ATP/WTA Tour turnaj
    "D": 24,   # Davis Cup / Fed Cup / United Cup
    "C": 20,   # Challenger
}
DEFAULT_K_FACTOR = 28

K_OVERALL_SCALE = 0.6   # overall rating se hýbe pomaleji než surface rating
SURFACE_WEIGHT = 0.65   # váha surface ratingu ve výsledné směsi (zbytek = overall)

SURFACE_FIELDS = {
    "hard": "elo_hard",
    "clay": "elo_clay",
    "grass": "elo_grass",
    "carpet": "elo_carpet",
}


@dataclass
class PlayerRating:
    player_id: int
    elo_overall: float = INITIAL_ELO
    elo_hard: float = INITIAL_ELO
    elo_clay: float = INITIAL_ELO
    elo_grass: float = INITIAL_ELO
    elo_carpet: float = INITIAL_ELO
    matches_played_total: int = 0

    def surface_elo(self, surface: str) -> float:
        return getattr(self, SURFACE_FIELDS.get(surface, "elo_hard"), self.elo_overall)

    def blended_elo(self, surface: str) -> float:
        """Směs surface + overall ratingu — viz docstring modulu."""
        s = self.surface_elo(surface)
        return SURFACE_WEIGHT * s + (1 - SURFACE_WEIGHT) * self.elo_overall


def expected_score(rating_a: float, rating_b: float) -> float:
    """Standardní Elo win-probability funkce."""
    return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))


def win_probability(player_a: PlayerRating, player_b: PlayerRating, surface: str) -> float:
    """Appčin vlastní odhad pravděpodobnosti výhry player_a nad player_b na daném povrchu."""
    ra = player_a.blended_elo(surface)
    rb = player_b.blended_elo(surface)
    return expected_score(ra, rb)


def _k_factor(tourney_level: Optional[str]) -> int:
    return K_FACTOR_BY_LEVEL.get((tourney_level or "").upper(), DEFAULT_K_FACTOR)


def update_ratings(
    winner: PlayerRating,
    loser: PlayerRating,
    surface: str,
    tourney_level: Optional[str] = None,
) -> None:
    """
    Aktualizuje ratingy IN PLACE po jednom odehraném zápase. Appka tohle
    volá sekvenčně, chronologicky, při přepočtu celého historického
    datasetu (viz data_ingest.py) — pořadí zápasů je důležité, Elo je
    stavový model.
    """
    k = _k_factor(tourney_level)
    surface_field = SURFACE_FIELDS.get(surface)

    # --- overall rating ---
    expected_w = expected_score(winner.elo_overall, loser.elo_overall)
    delta_overall = k * K_OVERALL_SCALE * (1 - expected_w)
    winner.elo_overall += delta_overall
    loser.elo_overall -= delta_overall

    # --- surface rating (jen pokud appka povrch zná) ---
    if surface_field is not None:
        w_surface = winner.surface_elo(surface)
        l_surface = loser.surface_elo(surface)
        expected_w_surface = expected_score(w_surface, l_surface)
        delta_surface = k * (1 - expected_w_surface)
        setattr(winner, surface_field, w_surface + delta_surface)
        setattr(loser, surface_field, l_surface - delta_surface)

    winner.matches_played_total += 1
    loser.matches_played_total += 1


class EloEngine:
    """
    Drží ratingy všech hráčů v paměti během přepočtu historického
    datasetu. `players_by_external_id` appka po přepočtu uloží zpět do
    DB (viz data_ingest.py) — v běžném provozu appka ratingy jen ČTE
    z DB (players.elo_*), tenhle engine appka spouští jen při importu/
    přepočtu historie, ne na každý request.
    """

    def __init__(self) -> None:
        self._ratings: dict[str, PlayerRating] = {}

    def get_or_create(self, external_id: str) -> PlayerRating:
        if external_id not in self._ratings:
            self._ratings[external_id] = PlayerRating(player_id=0)
        return self._ratings[external_id]

    def process_match(
        self,
        winner_external_id: str,
        loser_external_id: str,
        surface: str,
        tourney_level: Optional[str] = None,
    ) -> None:
        winner = self.get_or_create(winner_external_id)
        loser = self.get_or_create(loser_external_id)
        update_ratings(winner, loser, surface, tourney_level)

    def all_ratings(self) -> dict[str, PlayerRating]:
        return self._ratings
