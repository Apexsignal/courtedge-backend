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
4. Appka vezme 1 až MAX_TICKET_LEGS nejjistějších kandidátů z RŮZNÝCH
   zápasů, bez ohledu na to, jaký z toho vyjde kombinovaný kurz.
   Appka to dřív měla svázané pevným pásmem 2,00–3,00, ale appka
   zjistila, že vysoká jistota a vysoký kurz jdou málokdy dohromady
   (bookmaker appce dává skoro stejnou jistotu jako appka) — appka radši
   volí jistotu, ať appka kurz vyjde jakýkoliv.
5. Appka PŘESTANE přidávat další leg, jakmile by appku stáhl kombinovaná
   jistota (součin jistoty všech legů — appka musí trefit VŠECHNY, aby
   tiket vyhrál) pod MIN_COMBINED_PROBABILITY. Appka radši pošle tiket
   s míň výběry, než aby appka uměle stavěla kombinaci, co appku sama
   dělá pravděpodobnější PROHRU než výhru (viz README, "Analýza prvních
   reálných tiketů" — appka na tomhle uvízla u 5 z 6 prvních appčiných
   tiketů).

Appka i s tímhle přístupem dlouhodobě potřebuje, aby model byl LEPŠÍ než
náhoda — jinak appka prohrává o marži bookmakera stejně jako klasický
edge přístup (viz README). Rozdíl je v komunikaci k zákazníkovi
("nejjistější tipy", ne "tržní neefektivita"), ne v tom, že by appka
nemusela mít funkční model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

MIN_TICKET_LEGS = 1
MAX_TICKET_LEGS = 3


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


MIN_COMBINED_PROBABILITY = 0.45
# Appka appčiny první reálné tikety appka porovnala se skutečnými
# výsledky (2026-08-12, viz README "Analýza prvních reálných tiketů")
# — appka bere DO tiketu i legy appka jen proto, že appka MAX_TICKET_LEGS
# dovoluje o jeden víc, i když appka kombinovaná jistota (součin appky
# jistoty všech legů) appku tím padla pod 45 % — appka pak prohru měla
# pravděpodobnější než výhru, i kdyby appčin odhad byl na každý
# JEDNOTLIVÝ leg správně. Appka radši zastaví přidávání dalšího legu,
# než aby appku takhle sama sobě podráželo nohy.


def build_ticket(ranked_candidates: list[Candidate]) -> Optional[BuiltTicket]:
    """
    Appka bere kandidáty odshora žebříčku (nejjistější první, jeden na
    zápas — appka to očekává už deduplikované přes select_candidates) a
    přidává je do tiketu JEDEN PO DRUHÉM, dokud appku nedojdou kandidáti,
    appka nenarazí na MAX_TICKET_LEGS, nebo by další leg appku stáhl
    kombinovanou jistotu pod MIN_COMBINED_PROBABILITY (viz konstanta
    výše). První leg appka vezme vždycky, i kdyby byl sám pod tou
    hranicí — appka nikdy nevrátí prázdný tiket, jen kvůli tomu.
    Appka vrátí None, pokud appka nemá ani MIN_TICKET_LEGS použitelný
    kandidát.
    """
    usable = [c for c in ranked_candidates if c.market_odds is not None and c.market_odds > 1.0]
    if len(usable) < MIN_TICKET_LEGS:
        return None

    legs: list[Candidate] = []
    combined_probability = 1.0
    combined_odds = 1.0
    for candidate in usable:
        if len(legs) >= MAX_TICKET_LEGS:
            break
        next_combined_probability = combined_probability * candidate.model_probability
        if legs and next_combined_probability < MIN_COMBINED_PROBABILITY:
            break
        legs.append(candidate)
        combined_probability = next_combined_probability
        combined_odds *= candidate.market_odds

    return BuiltTicket(legs=legs, total_odds=round(combined_odds, 3))
