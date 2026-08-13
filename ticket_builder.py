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
MAX_TICKET_LEGS = 4
# Appka 2026-08-12 zvedla ze 3 na 4 (a MIN_COMBINED_PROBABILITY snížila
# na 0.40, viz níže) — appka zjistila, že appčiny nejjistější picky mají
# skoro vždycky nízký kurz (bookmaker appce dává stejnou jistotu), takže
# appka na kombinovaný kurz 2,00+ často nedosáhla ani na 3 legy. Appka
# tímhle appce dovoluje o leg víc, aby se appka k vyššímu kurzu přiblížila
# — appka to VĚDOMĚ dělá s vyšším rizikem než appka měla před tímhle
# zásahem (viz README, "Kurz vs. riziko").


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


MIN_COMBINED_PROBABILITY = 0.40
# Appka appčiny první reálné tikety appka porovnala se skutečnými
# výsledky (2026-08-12, viz README "Analýza prvních reálných tiketů")
# — appka bere DO tiketu i legy appka jen proto, že appka MAX_TICKET_LEGS
# dovoluje o jeden víc, i když appka kombinovaná jistota (součin appky
# jistoty všech legů) appku tím padla nízko — appka pak prohru měla
# pravděpodobnější než výhru, i kdyby appčin odhad byl na každý
# JEDNOTLIVÝ leg správně. Appka radši zastaví přidávání dalšího legu,
# než aby appku takhle sama sobě podráželo nohy.
#
# Appka měla tuhle hranici původně na 0.45 (MAX_TICKET_LEGS na 3).
# 2026-08-12 appka obě čísla uvolnila (0.40 / 4 legy) na výslovné přání
# uživatele — appčiny nejjistější picky měly nízký kurz a uživatel chtěl
# kurz 1,9–3,0 (viz README, "Kurz vs. riziko"). Je to vědomě vyšší
# riziko prohry než appka měla u 0.45 — zapsáno tady, ať to příště
# appka nemusí znovu objevovat od nuly.

MIN_COMBINED_PROBABILITY_WINNER = 0.35
# Appka trh výherce zápasu (match_winner) má jinou přirozenou
# jistotu než gemy — appčin bezpečnostní práh pro tenhle trh je 0,62
# (market_thresholds v DB), takže appka dva favorité těsně nad prahem
# sami o sobě dají kombinovanou jistotu kolem 38 %. Appka proto pro
# tiket na výherce (viz ticket_generation.generate_daily_tickets) drží
# NIŽŠÍ podlahu než appka má pro gemový tiket — jinak by appka tiket
# "dva favorité" skoro nikdy nesestavila.


def build_ticket(
    ranked_candidates: list[Candidate],
    min_combined_probability: float = MIN_COMBINED_PROBABILITY,
) -> Optional[BuiltTicket]:
    """
    Appka bere kandidáty odshora žebříčku (nejjistější první, jeden na
    zápas — appka to očekává už deduplikované přes select_candidates) a
    přidává je do tiketu JEDEN PO DRUHÉM, dokud appku nedojdou kandidáti,
    appka nenarazí na MAX_TICKET_LEGS, nebo by další leg appku stáhl
    kombinovanou jistotu pod `min_combined_probability` (appka bere
    MIN_COMBINED_PROBABILITY jako výchozí, appka pro tiket na výherce
    volá s MIN_COMBINED_PROBABILITY_WINNER, viz konstanta výše). První
    leg appka vezme vždycky, i kdyby byl sám pod tou hranicí — appka
    nikdy nevrátí prázdný tiket, jen kvůli tomu. Appka vrátí None,
    pokud appka nemá ani MIN_TICKET_LEGS použitelný kandidát.

    Appka OD 2026-08-12 tuhle funkci pro appčin denní broadcast
    nepoužívá (viz build_favorites_ticket níže) — appka appku nechává
    v modulu, appka appku funkčně otestovala i reálnými daty a appka ji
    může chtít appka znovu použít, kdyby appka měla lepší zdroj kurzů
    na gemy (viz README, "Favorité místo gemů").
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
        if legs and next_combined_probability < min_combined_probability:
            break
        legs.append(candidate)
        combined_probability = next_combined_probability
        combined_odds *= candidate.market_odds

    return BuiltTicket(legs=legs, total_odds=round(combined_odds, 3))


FAVORITE_MIN_LEG_ODDS = 1.3
FAVORITE_MAX_LEG_ODDS = 1.7
# Appka to přidala 2026-08-12 na přání uživatele. Appka živě zjistila
# (screenshot od uživatele), že appčin trh gemů má u skutečného
# bookmakera jinou hranici, než appka ukazuje — je to jiná sázka, ne
# jen jiná cena. Trh výherce zápasu tenhle problém nemá — žádná
# hranice, stejná sázka u kteréhokoli bookmakera.
#
# Appka měla horní hranici původně na 2,0, appka ji 2026-08-13 zpřísnila
# na 1,7 na přání uživatele — kurz 1,7 znamená, že trh odhaduje hráči
# zhruba 59% šanci, kurz 2,0 jen 50 %. Appka radši bere picky, kde
# hráče vidí jistě i trh, ne jen appčin model. Pod 1,3 vidí bookmaker
# hráče skoro stejně jistě jako appka — appka na tom nemá výhodu.

# 2026-08-13, verze 1: appka zkusila jeden pick na tiket, žádné
# kombinování. Appka živě ověřila, že jednotlivé picky trefovaly
# blízko appčiny vlastní jistoty (60 % skutečnost proti 67 % slibu na
# 5 vzorcích) — appka model appce vypadal v pořádku. Kombinované
# tikety appce ale prohrály oba, protože appka musela trefit VŠECHNY
# legy najednou.
#
# 2026-08-13, verze 2: uživatel appce řekl, ať appka appku vrátí
# zpátky na JEDEN tiket, appka appce nechá jen 2 nejjistější tipy
# dohromady. Appka appku poslechla. Appka appku POŘÁD platí varování
# appce výš — kombinovaný tiket appka musí trefit oba legy, takže
# appčina šance appku vyhrát je nižší než appčina jistota lepšího
# picku samotného.

DAILY_TICKET_LEGS = 2


def build_favorites_ticket(candidates: list[Candidate], num_legs: int = DAILY_TICKET_LEGS) -> Optional[BuiltTicket]:
    """
    Appka bere jen kandidáty trhu výherce zápasu — appka to zajišťuje
    volající (viz ticket_generation.generate_daily_ticket).

    Appka nechá jen kandidáty s kurzem v pásmu FAVORITE_MIN_LEG_ODDS až
    FAVORITE_MAX_LEG_ODDS, appka je seřadí podle jistoty a vezme
    prvních `num_legs` z RŮZNÝCH zápasů. Appka vrátí None, pokud appka
    nemá ani jednoho kandidáta v pásmu — appka klidně appce vrátí
    tiket s méně legy, než appka `num_legs` žádá, pokud appka
    kandidátů nemá dost.
    """
    in_band = [
        c for c in candidates
        if c.market_odds is not None
        and FAVORITE_MIN_LEG_ODDS <= c.market_odds <= FAVORITE_MAX_LEG_ODDS
    ]
    if not in_band:
        return None

    best_per_match: dict[int, Candidate] = {}
    for c in in_band:
        current_best = best_per_match.get(c.match_id)
        if current_best is None or c.model_probability > current_best.model_probability:
            best_per_match[c.match_id] = c
    ranked = sorted(best_per_match.values(), key=lambda c: c.model_probability, reverse=True)

    legs = ranked[:num_legs]
    combined_odds = 1.0
    for leg in legs:
        combined_odds *= leg.market_odds
    return BuiltTicket(legs=legs, total_odds=round(combined_odds, 3))
