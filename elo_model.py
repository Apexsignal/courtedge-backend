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


# --- Nejistota ratingu (Glicko-inspirované).
#
# Appka si 2026-08-11 živě všimla problému: nejjistější tipy skoro vždy
# patřily hráčům z kvalifikací. Právě tihle hráči mají nejméně
# odehraných zápasů, takže appka o nich ví nejméně. Elo appka dosud
# bralo jako pevné číslo bez ohledu na to, kolik za ním stojí dat —
# hráč se 300 zápasy a hráč s 15 zápasy dostávali stejnou váhu, pokud
# měli stejný rating. To appka teď opravuje: rating hráče s málo daty
# appka bere s menší důvěrou a výslednou pravděpodobnost posouvá blíž
# k 50 %.
CONFIDENCE_HALF_LIFE_MATCHES = 20  # appka dosáhne poloviny plné důvěry po tolika odehraných zápasech


def rating_confidence(matches_played_total: int) -> float:
    """
    Appka vrátí číslo mezi 0 a 1. Appka bere 0 jako "appka o hráči neví
    nic" a 1 jako "appka ratingu věří naplno". Hodnota roste s počtem
    odehraných zápasů a asymptoticky se blíží k 1, nikdy ji nedosáhne
    přesně. Appka příklad: 20 zápasů dá důvěru 50 %, 100 zápasů dá
    zhruba 83 %.
    """
    n = max(matches_played_total, 0)
    return n / (n + CONFIDENCE_HALF_LIFE_MATCHES)


def combined_confidence(player_a: PlayerRating, player_b: PlayerRating) -> float:
    """Appka bere SLABŠÍ ze dvou důvěr — appka predikce zápasu je jen
    tak spolehlivá, jak spolehlivě appka zná toho hůř zmapovaného
    hráče, i kdyby appka o druhém věděla sebevíc."""
    return min(rating_confidence(player_a.matches_played_total), rating_confidence(player_b.matches_played_total))


def win_probability(
    player_a: PlayerRating,
    player_b: PlayerRating,
    surface: str,
    fatigue_elo_adjustment: float = 0.0,
    confidence_multiplier: float = 1.0,
) -> float:
    """
    Appčin vlastní odhad pravděpodobnosti výhry player_a nad player_b na
    daném povrchu. Pokud appka o některém z hráčů nemá dost dat (viz
    combined_confidence výše), odhad se posune blíž k 50 %. Appka radši
    přizná nejistotu, než by tvrdila jistotu, kterou nemá.

    `fatigue_elo_adjustment` a `confidence_multiplier` appka bere jako
    volitelné vstupy z head_to_head.py (signál únavy/odpočinku, viz
    tam) — appka je tady nechává na 0.0/1.0, ať appka historický
    přepočet Elo (data_ingest.py) a backtest (scripts/backtest_calibration.py)
    nemusí o téhle appce vůbec vědět.
    """
    ra = player_a.blended_elo(surface) + fatigue_elo_adjustment
    rb = player_b.blended_elo(surface)
    raw = expected_score(ra, rb)
    confidence = combined_confidence(player_a, player_b) * confidence_multiplier
    return 0.5 + confidence * (raw - 0.5)


def _k_factor(tourney_level: Optional[str]) -> int:
    return K_FACTOR_BY_LEVEL.get((tourney_level or "").upper(), DEFAULT_K_FACTOR)


def update_ratings(
    winner: PlayerRating,
    loser: PlayerRating,
    surface: Optional[str],
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
        surface: Optional[str],
        tourney_level: Optional[str] = None,
    ) -> None:
        winner = self.get_or_create(winner_external_id)
        loser = self.get_or_create(loser_external_id)
        update_ratings(winner, loser, surface, tourney_level)

    def all_ratings(self) -> dict[str, PlayerRating]:
        return self._ratings
