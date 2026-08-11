"""
market_models.py — modely pro trhy "over/under gemů" a "over/under es".

Podle poznámek k produktové strategii jde o modelově NEJNÁROČNĚJŠÍ dva
trhy appky (výherce zápasu = hotový princip, viz elo_model.py). Appka je
odvozuje z historických dat, ne z hotového API:

- **Gemy (total_games):** menší Elo gap mezi hráči → statisticky delší/
  vyrovnanější zápas → víc gemů. Appka to modeluje jako normální
  rozdělení kolem baseline počtu gemů (podle best_of a povrchu — tráva
  má kratší výměny/víc esa/míň breaků, tedy typicky MÍŇ gemů na zápas
  než antuka), posunuté podle velikosti Elo gapu.
- **Esa (total_aces):** appka sečte per-hráčovský ace rate (es na
  servisní hru, surface-adjusted) obou hráčů, vynásobí odhadovaným
  počtem servisních her v zápase (odvozeno ze stejného game-total
  modelu) → očekávaný součet es. Rozdělení kolem něj appka modeluje
  jako Poissonovo (počet es je diskrétní, řídký jev na servisní hru).

Appka NEPOROVNÁVÁ tohle s tržním kurzem jako filtr "beru/neberu" — jen
počítá vlastní pravděpodobnost pro danou hranici (line), viz
ticket_builder.py pro to, jak appka kandidáty řadí a vybírá.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# --- Baseline počet gemů na zápas podle povrchu a best_of ---
# Hrubý startovní odhad (typický ATP/WTA průměr) — appka by tohle měla
# rekalibrovat na skutečném historickém průměru z ingestovaných dat
# (viz data_ingest.py), tyhle konstanty jsou jen fallback, dokud appka
# nemá dost dat k výpočtu.
BASELINE_GAMES = {
    ("hard", 3): 22.0,
    ("hard", 5): 36.0,
    ("clay", 3): 22.5,
    ("clay", 5): 37.0,
    ("grass", 3): 21.0,
    ("grass", 5): 34.5,
    ("carpet", 3): 21.5,
    ("carpet", 5): 35.5,
}
GAMES_ELO_GAP_SENSITIVITY = 0.01   # o kolik gemů appka posune baseline na 100 bodů Elo gapu
GAMES_STD_DEV = 4.2                 # rozptyl kolem odhadovaného počtu gemů (normální rozdělení)

# Servisních her na hráče appka odhaduje jako polovinu total_games,
# zaokrouhleno — hrubé přiblížení, appka nerozlišuje breaky zvlášť.
AVG_ACE_RATE_FALLBACK = 0.08  # es na servisní hru, použije appka, když hráč nemá dost dat


def _normal_cdf(x: float, mean: float, std_dev: float) -> float:
    """P(X <= x) pro normální rozdělení — appka bez scipy, jen erf ze stdlib."""
    z = (x - mean) / (std_dev * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) pro Poissonovo rozdělení — appka počítá přímo součtem, es v zápase je malé číslo."""
    if lam <= 0:
        return 1.0
    total = 0.0
    term = math.exp(-lam)
    total += term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return min(total, 1.0)


@dataclass
class GamesTotalEstimate:
    expected_games: float
    std_dev: float

    def prob_over(self, line: float) -> float:
        """P(total_games > line). Appka bere `line + 0.5`, protože total_games je celé číslo a appčiny linie jsou vždy X.5."""
        return 1 - _normal_cdf(line + 0.5, self.expected_games, self.std_dev)

    def prob_under(self, line: float) -> float:
        return 1 - self.prob_over(line)


def estimate_total_games(
    elo_a: float,
    elo_b: float,
    surface: str,
    best_of: int = 3,
) -> GamesTotalEstimate:
    """
    Odhad rozdělení celkového počtu gemů v zápase. Menší |elo_a - elo_b|
    → appka očekává vyrovnanější a tím spíš delší zápas (víc gemů),
    velký gap → appka očekává kratší zápas.
    """
    baseline = BASELINE_GAMES.get((surface, best_of), BASELINE_GAMES.get((surface, 3), 22.0))
    gap = abs(elo_a - elo_b)
    # Malý gap = zápas blíž baseline (nebo mírně nad), velký gap appka
    # posouvá dolů (jednostrannější zápas, kratší sety).
    adjustment = -GAMES_ELO_GAP_SENSITIVITY * gap
    expected_games = baseline + adjustment
    return GamesTotalEstimate(expected_games=max(expected_games, 12.0), std_dev=GAMES_STD_DEV)


@dataclass
class AcesTotalEstimate:
    expected_aces: float

    def prob_over(self, line: float) -> float:
        k = math.floor(line)  # P(total > line) = 1 - P(total <= floor(line))
        return 1 - _poisson_cdf(k, self.expected_aces)

    def prob_under(self, line: float) -> float:
        return 1 - self.prob_over(line)


def estimate_total_aces(
    ace_rate_a: Optional[float],
    ace_rate_b: Optional[float],
    games_estimate: GamesTotalEstimate,
) -> AcesTotalEstimate:
    """
    Očekávaný součet es obou hráčů = (ace_rate_a + ace_rate_b) *
    odhadovaný počet servisních her v zápase. Servisní hry appka odhaduje
    jako total_games (obě servisní řady dohromady zhruba odpovídají
    total_games, protože každý gem má jednoho podávajícího).
    """
    rate_a = ace_rate_a if ace_rate_a is not None else AVG_ACE_RATE_FALLBACK
    rate_b = ace_rate_b if ace_rate_b is not None else AVG_ACE_RATE_FALLBACK
    expected_service_games = games_estimate.expected_games
    expected_aces = (rate_a + rate_b) * expected_service_games / 2
    return AcesTotalEstimate(expected_aces=max(expected_aces, 0.5))
