"""
ticket_builder.py — "confidence ranking" výběr kandidátů a stavba tiketu.

Princip (viz kontext appky, nahrazuje klasický value/edge betting):

1. Appka spočítá vlastní pravděpodobnost pro KAŽDÉHO kandidáta (leg) ve
   VŠECH třech trzích (elo_model.py pro výherce, market_models.py pro
   gemy/esa).
2. Kandidáti appka seřadí ČISTĚ podle vlastní jistoty (model_probability),
   nejjistější první — appka NEPOROVNÁVÁ s tržní pravděpodobností jako
   filtr "beru/neberu". Tržní kurz appka použije až v kroku 4.
3. Appka aplikuje bezpečnostní filtry PŘED řazením (ne až po něm):
   - vyřadit hráče s nedávnou historií skreče (retirement)
   - vyřadit hráče s málo odehranými zápasy (nedůvěryhodný rating)
   - vyřadit kandidáty pod prahem minimální jistoty pro daný trh
     (market_thresholds v DB, různé pro tři trhy)
4. Appka vezme 2 nejjistější kandidáty z RŮZNÝCH zápasů a spočítá
   kombinovaný kurz (součin tržních kurzů obou legů). Pokud výsledek
   nespadá do pásma 2,00–3,00, appka zkusí další kombinace (další
   nejjistější pár) — ne že by appka kurz kandidátů uměle upravovala.

Appka i s tímhle přístupem dlouhodobě potřebuje, aby model byl LEPŠÍ než
náhoda — jinak appka prohrává o marži bookmakera stejně jako klasický
edge přístup (viz README). Rozdíl je v komunikaci k zákazníkovi
("nejjistější tipy", ne "tržní neefektivita"), ne v tom, že by appka
nemusela mít funkční model.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

TICKET_ODDS_MIN = 2.00
TICKET_ODDS_MAX = 3.00
TICKET_LEGS = 2


@dataclass
class Candidate:
    match_id: int
    market_code: str          # 'match_winner' | 'total_games' | 'total_aces'
    selection: str             # 'player_a' | 'player_b' | 'over' | 'under'
    line: Optional[float]
    model_probability: float
    market_odds: Optional[float]   # None = appka nemá tržní kurz pro tenhle leg (viz odds_provider.py, esa nemají bookmaker market)


@dataclass
class SafetyContext:
    """Appka potřebuje tyhle údaje o obou hráčích v zápase, aby mohla
    aplikovat bezpečnostní filtry na kandidáta."""
    match_id: int
    player_a_matches_played_12mo: int
    player_b_matches_played_12mo: int
    player_a_recent_retirements: int
    player_b_recent_retirements: int


@dataclass
class MarketThreshold:
    market_code: str
    min_confidence: float
    min_matches_played_12mo: int


MAX_RECENT_RETIREMENTS_ALLOWED = 0  # appka vyřazuje hráče s JAKOUKOLI nedávnou historií skreče (konzervativní start)


def passes_safety_filters(
    candidate: Candidate,
    context: SafetyContext,
    thresholds: dict[str, MarketThreshold],
) -> bool:
    threshold = thresholds.get(candidate.market_code)
    if threshold is None:
        return False  # appka bez prahu radši trh úplně vynechá, než aby hádala

    if candidate.model_probability < threshold.min_confidence:
        return False

    if (
        context.player_a_matches_played_12mo < threshold.min_matches_played_12mo
        or context.player_b_matches_played_12mo < threshold.min_matches_played_12mo
    ):
        return False  # nedůvěryhodný rating — appka na málo zápasech Elo/ace rate nevěří

    if (
        context.player_a_recent_retirements > MAX_RECENT_RETIREMENTS_ALLOWED
        or context.player_b_recent_retirements > MAX_RECENT_RETIREMENTS_ALLOWED
    ):
        return False  # nedávná historie skreče — appka radši vynechá než riskuje void/prohru kvůli zdravotnímu stavu

    return True


MIN_USABLE_ODDS = 1.15
# Appka objevila živě, že market_models.py u jednoho hodně jednostranného
# zápasu vygeneruje kandidáty na DESÍTKY různých hranic gemů (appka nabízí
# každou hranici, co appka najde tržní kurz) — appka to bez zásahu vidí
# jako "8 nejjistějších tipů", i když je to jeden signál osmkrát. Appka
# navíc takové extrémní kandidáty appka pozná podle kurzu blízko 1,00 —
# to znamená, že i BOOKMAKER appku vidí skoro jistě, appka na tom nemá
# žádnou vlastní výhodu a do tiketu appku takový leg stejně nepoužije
# (kombinovaný kurz by appku appku stáhl pod 2,00).


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Čistě podle vlastní jistoty modelu, nejjistější první. Appka
    filtry aplikuje PŘED voláním týhle funkce (viz passes_safety_filters)."""
    return sorted(candidates, key=lambda c: c.model_probability, reverse=True)


def select_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """
    Appka tímhle žebříček čistí od dvou zkreslení, než appka kandidáty
    seřadí a stavbě tiketu předá:

    1. Vyřadí kurzy appka pod MIN_USABLE_ODDS — appka na nich nemá
       žádnou informační výhodu (bookmaker appce dává skoro stejnou
       jistotu) a do kombinovaného kurzu appka stejně nepřispějí.
    2. Nechá jen NEJJISTĚJŠÍHO kandidáta z každého zápasu — appka jinak
       jeden jednostranný zápas appce vyplní žebříček desítkami variant
       (různé hranice gemů/es), což appku klame, že appka má spoustu
       nezávislých silných signálů, místo jednoho.
    """
    usable = [c for c in candidates if c.market_odds is not None and c.market_odds >= MIN_USABLE_ODDS]

    best_per_match: dict[int, Candidate] = {}
    for c in usable:
        current_best = best_per_match.get(c.match_id)
        if current_best is None or c.model_probability > current_best.model_probability:
            best_per_match[c.match_id] = c

    return rank_candidates(list(best_per_match.values()))


@dataclass
class BuiltTicket:
    legs: list[Candidate]
    total_odds: float


def build_ticket(ranked_candidates: list[Candidate]) -> Optional[BuiltTicket]:
    """
    Appka projde kandidáty odshora (nejjistější první) a hledá první
    dvojici z RŮZNÝCH zápasů, jejíž kombinovaný tržní kurz padne do
    pásma 2,00–3,00. Appka preferuje páry blíž vrcholu žebříčku (víc
    jisté), ne první platnou kombinaci odkudkoli v seznamu — proto
    appka iteruje přes dvojice v pořadí rostoucí "vzdálenosti od
    vrcholu" (součet indexů), ne přes obyčejný itertools.combinations
    v původním pořadí, kde by první nalezená kombinace mohla obsahovat
    kandidáta hluboko v žebříčku.
    """
    usable = [c for c in ranked_candidates if c.market_odds is not None and c.market_odds > 1.0]
    n = len(usable)
    if n < TICKET_LEGS:
        return None

    pairs_by_rank_sum = sorted(
        itertools.combinations(range(n), TICKET_LEGS),
        key=lambda idx_pair: sum(idx_pair),
    )

    for i, j in pairs_by_rank_sum:
        leg_a, leg_b = usable[i], usable[j]
        if leg_a.match_id == leg_b.match_id:
            continue  # appka nechce 2 výběry ze stejného zápasu (korelované, ne nezávislé)
        combined_odds = round(leg_a.market_odds * leg_b.market_odds, 3)
        if TICKET_ODDS_MIN <= combined_odds <= TICKET_ODDS_MAX:
            return BuiltTicket(legs=[leg_a, leg_b], total_odds=combined_odds)

    return None
