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

## Kalibrace (2026-08-11, `scripts/backtest_calibration.py`)

Appka první verzi konstant níže měla jako hrubý odhad ("appka by tohle
měla rekalibrovat, až bude mít data"). Appka teď MÁ data — walk-forward
backtest na 6184 zápasech (ATP+WTA, 2025-08 až 2026-08, appka na
každý zápas počítala predikci JEN z toho, co appka věděla PŘED ním) —
a appčin původní odhad byl výrazně mimo:

- **Gemy:** appka měla rozptyl 4,2 gemu, skutečnost je ~7,5–9,0 (podle
  povrchu) — appka byla skoro dvakrát přehnaně sebevědomá. Appka měla
  citlivost na Elo gap -0,01 gemu/bod, realita ukazuje jen ~-0,0025 —
  rozdíl v síle appka ovlivňuje délku zápasu mnohem míň, než appka
  čekala. Appka taky odhadovala systematicky o ~2–4 gemy MÍŇ, než se
  fakt hraje.
- **Esa:** appka počítala s Poissonovým rozdělením (rozptyl = průměr).
  Skutečný rozptyl je ~6,7× vyšší — esa appka kolísají zápas od zápasu
  mnohem víc, než Poisson připouští. Appka navíc odhadovala o ~11 %
  méně es, než se fakt hraje.
- **Výherce (Elo)** appka nemusela měnit — Brier score 0,227 (appka
  bere 0,25 jako "stejně dobré jako mince") a appka je v hlavním pásmu
  40–70 % rozumně kalibrovaná.

Appka narazila i na datovou chybu cestou: `api_tennis_ingest.py` appka
dřív VŠECHNY zápasy appce značila jako `best_of=3`, i appka Grand Slamy
(bo5) — appka to opravila, ale appka NEPŘEPOČÍTALA tenhle konkrétní
backtest znovu (stálo by appku další hodiny stahování) — appčino číslo
pro appka POVRCH TRÁVA je proto pravděpodobně mírně nadhodnocené
(Wimbledon appka kontaminoval "bo3 tráva" koš zápasy, co appka měly
mnohem víc gemů, protože byly bo5) — appka tam vzala konzervativnější
hodnotu, ne appka syrové naměřené číslo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# --- Baseline počet gemů na zápas podle povrchu a best_of ---
# bo3 appka má přímo z backtestu (viz docstring modulu výše, hard/clay
# appka bere naměřený průměr, grass appka bere konzervativnější odhad
# kvůli bo5 kontaminaci). bo5 appka nemá čistá data (Grand Slamy appka
# dřív appka nesprávně padaly appka do bo3 koše) — appka je odvodila
# proporčně appka ze starého poměru bo5/bo3 (~1,64×), dokud appka
# nespustí čistý backtest po opravě best_of detekce.
BASELINE_GAMES = {
    ("hard", 3): 22.9,
    ("hard", 5): 37.5,
    ("clay", 3): 23.2,
    ("clay", 5): 38.1,
    ("grass", 3): 24.0,
    ("grass", 5): 39.4,
    ("carpet", 3): 21.5,  # appka nemá reálná data (carpet je na turu prakticky vymřelý) — appka nechává starý odhad
    ("carpet", 5): 35.5,
}
GAMES_ELO_GAP_SENSITIVITY = 0.0025  # naměřeno backtestem — o kolik gemů appka posune baseline na 100 bodů Elo gapu (appka měla 0.01, 4× moc)

# Rozptyl appka teď appka drží per-povrch (appka to backtest ukázal jako
# povrch-specifické — tráva kolísá nejvíc). DEFAULT appka použije pro
# povrch appka nezná (carpet, appka na něj nemá dost dat).
GAMES_STD_DEV_BY_SURFACE = {"hard": 6.8, "clay": 7.24, "grass": 8.98}
GAMES_STD_DEV_DEFAULT = 7.5

# Servisních her na hráče appka odhaduje jako polovinu total_games,
# zaokrouhleno — hrubé přiblížení, appka nerozlišuje breaky zvlášť.
AVG_ACE_RATE_FALLBACK = 0.08  # es na servisní hru, použije appka, když hráč nemá dost dat

# Backtest ukázal appčin surový odhad es systematicky o ~11 % nízko —
# appka to koriguje multiplikativně, ne aditivně (es appka roste s
# délkou zápasu, procentuální korekce appka sedí líp než pevné číslo).
ACES_BIAS_CORRECTION = 1.121

# Poissonův předpoklad (rozptyl == průměr) appka backtestem vyvrátila —
# es mají ~6,7× větší rozptyl. Appka proto přešla z Poissonova na
# normální rozdělení s nafouknutým rozptylem (appka na to má hotové
# `_normal_cdf` z games modelu) — rozumná náhrada za pořádný negativně
# binomický model, který appka kvůli jedné konstantě zatím nestaví.
ACES_OVERDISPERSION = 6.66


def _normal_cdf(x: float, mean: float, std_dev: float) -> float:
    """P(X <= x) pro normální rozdělení — appka bez scipy, jen erf ze stdlib."""
    z = (x - mean) / (std_dev * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


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
    baseline = BASELINE_GAMES.get((surface, best_of), BASELINE_GAMES.get((surface, 3), 22.9))
    gap = abs(elo_a - elo_b)
    # Malý gap = zápas blíž baseline (nebo mírně nad), velký gap appka
    # posouvá dolů (jednostrannější zápas, kratší sety).
    adjustment = -GAMES_ELO_GAP_SENSITIVITY * gap
    expected_games = baseline + adjustment
    std_dev = GAMES_STD_DEV_BY_SURFACE.get(surface, GAMES_STD_DEV_DEFAULT)
    return GamesTotalEstimate(expected_games=max(expected_games, 12.0), std_dev=std_dev)


@dataclass
class AcesTotalEstimate:
    expected_aces: float
    std_dev: float

    def prob_over(self, line: float) -> float:
        """P(total_aces > line). Appka dřív počítala s Poissonovým
        rozdělením (`_poisson_cdf`) — backtest ukázal, že es appka
        mají ~6,7× větší rozptyl, než Poisson předpokládá (viz
        ACES_OVERDISPERSION výše), appka proto přešla na normální
        aproximaci se stejným nafouknutým rozptylem jako u gemů."""
        return 1 - _normal_cdf(line + 0.5, self.expected_aces, self.std_dev)

    def prob_under(self, line: float) -> float:
        return 1 - self.prob_over(line)


def estimate_total_aces(
    ace_rate_a: Optional[float],
    ace_rate_b: Optional[float],
    games_estimate: GamesTotalEstimate,
) -> AcesTotalEstimate:
    """
    Očekávaný součet es obou hráčů = (ace_rate_a + ace_rate_b) *
    odhadovaný počet servisních her v zápase, korigováno appka
    ACES_BIAS_CORRECTION (appka backtestem zjistila, že appčin surový
    odhad byl systematicky o ~11 % nízko). Servisní hry appka odhaduje
    jako total_games (obě servisní řady dohromady zhruba odpovídají
    total_games, protože každý gem má jednoho podávajícího).
    """
    rate_a = ace_rate_a if ace_rate_a is not None else AVG_ACE_RATE_FALLBACK
    rate_b = ace_rate_b if ace_rate_b is not None else AVG_ACE_RATE_FALLBACK
    expected_service_games = games_estimate.expected_games
    expected_aces = (rate_a + rate_b) * expected_service_games / 2 * ACES_BIAS_CORRECTION
    expected_aces = max(expected_aces, 0.5)
    std_dev = math.sqrt(ACES_OVERDISPERSION * expected_aces)
    return AcesTotalEstimate(expected_aces=expected_aces, std_dev=std_dev)
