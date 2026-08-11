"""
head_to_head.py — appka tady bere vzájemnou historii (H2H) obou hráčů a
signál únavy/odpočinku z api-tennis.com metody `get_H2H`. Appka jedním
voláním dostane OBOJÍ najednou (`result.H2H` + appčiny poslední zápasy
`firstPlayerResults`/`secondPlayerResults` obou hráčů zvlášť), takže to
appka řeší v jednom modulu, ne zvlášť.

Appka rozlišuje DVA různé signály, protože appka na ně reaguje jinak:

1. **H2H poměr výher** — appka appčin Elo odhad posune blíž k tomu, jak
   spolu tihle dva konkrétní hráči hráli dřív. Appka tomu dá váhu, co
   roste s počtem vzájemných zápasů, ale appka ji shora omezuje
   (H2H_MAX_WEIGHT) — pár zápasů appce na jistotu nestačí, i kdyby
   appka je všechny vyhrál jeden hráč.
2. **Únava (odehrané zápasy za poslední týden)** — appka appčin Elo
   posune SMĚREM (penalizuje unavenějšího hráče), protože appka má
   důvod si myslet, kdo je zvýhodněný.
3. **Dlouhá pauza (45+ dní bez zápasu)** — appka naopak NEVÍ, jestli je
   hráč po pauze lepší nebo horší, než appčin rating říká (zranění,
   ztráta formy, nebo naopak odpočatý) — appka proto jen SNIŽUJE
   důvěru v rating (posun blíž k 50 %), stejný princip jako
   `elo_model.rating_confidence` u hráčů s málo odehranými zápasy.

Appka volání na api-tennis.com dělá ŽIVĚ při generování tiketu (ne
z appčiny historické CSV appky, viz data_ingest.py) — appka historii
jednotlivých zápasů nikde v DB neukládá, jen appčinu Elo agregaci.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import api_tennis_provider as provider

H2H_HALF_LIFE_MATCHES = 3   # appka dá H2H poměru poloviční váhu po tolika vzájemných zápasech
H2H_MAX_WEIGHT = 0.35       # appka H2H nikdy nenechá appku přebít víc než tímhle podílem nad appčiným Elo odhadem

RECENT_WINDOW_DAYS = 7            # appka v tomhle okně počítá "kolik zápasů appka hráč odehrál nedávno"
FATIGUE_ELO_PER_EXTRA_MATCH = 25  # appka penalizuje hráče s víc zápasy za posledních RECENT_WINDOW_DAYS dní než appka soupeř — hrubý odhad, appka to zatím neměla na čem zpětně otestovat

LONG_LAYOFF_DAYS = 45          # appka od tolika dní bez zápasu appku bere jako "appka nevím, v jaké je formě"
LAYOFF_CONFIDENCE_MULTIPLIER = 0.7  # appka tímhle násobí appčinu důvěru (viz elo_model.combined_confidence) za KAŽDÉHO hráče s dlouhou pauzou


def _parse_date(event_date: str) -> Optional[date]:
    try:
        return datetime.strptime((event_date or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass
class FormSignal:
    h2h_wins: int
    h2h_losses: int
    matches_last_7d: int
    days_since_last_match: Optional[int]

    @property
    def h2h_total(self) -> int:
        return self.h2h_wins + self.h2h_losses


@dataclass
class MatchupForm:
    player_a: FormSignal
    player_b: FormSignal


def _count_h2h(h2h_matches: list[dict], player_a_key: str) -> tuple[int, int]:
    """Appka appce vrátí (výhry player_a, prohry player_a) jen z DOHRANÝCH
    zápasů — appka appce nepředpokládá, že "First Player" v H2H záznamu
    je vždycky appčin player_a, appka to appce u KAŽDÉHO zápasu ověří
    přes player_key."""
    wins = losses = 0
    for m in h2h_matches:
        if m.get("event_status") not in provider.FINISHED_STATUSES:
            continue
        winner_side = m.get("event_winner")
        if winner_side not in ("First Player", "Second Player"):
            continue
        a_is_first = str(m.get("first_player_key")) == str(player_a_key)
        a_won = (winner_side == "First Player") == a_is_first
        if a_won:
            wins += 1
        else:
            losses += 1
    return wins, losses


def _recent_form(results: list[dict], as_of: date) -> tuple[int, Optional[int]]:
    """Appka appce vrátí (počet DOHRANÝCH zápasů za posledních
    RECENT_WINDOW_DAYS dní, dny od posledního dohraného zápasu) — appka
    počítá jen zápasy PŘED appčiným nadcházejícím zápasem (`as_of`)."""
    matches_recent = 0
    most_recent: Optional[date] = None
    for m in results:
        if m.get("event_status") not in provider.FINISHED_STATUSES:
            continue
        d = _parse_date(m.get("event_date", ""))
        if d is None or d >= as_of:
            continue
        if most_recent is None or d > most_recent:
            most_recent = d
        if (as_of - d).days <= RECENT_WINDOW_DAYS:
            matches_recent += 1
    days_since = (as_of - most_recent).days if most_recent else None
    return matches_recent, days_since


def fetch_matchup_form(player_a_key: str, player_b_key: str, match_date: date) -> MatchupForm:
    """Appka zavolá api-tennis.com JEDNOU za zápas — `get_H2H` appce dá
    vzájemnou historii i poslední výsledky OBOU hráčů najednou."""
    raw = provider.get_h2h(player_a_key, player_b_key)
    h2h_wins_a, h2h_losses_a = _count_h2h(raw.get("H2H") or [], player_a_key)
    a_recent, a_days_since = _recent_form(raw.get("firstPlayerResults") or [], match_date)
    b_recent, b_days_since = _recent_form(raw.get("secondPlayerResults") or [], match_date)
    return MatchupForm(
        player_a=FormSignal(h2h_wins=h2h_wins_a, h2h_losses=h2h_losses_a, matches_last_7d=a_recent, days_since_last_match=a_days_since),
        player_b=FormSignal(h2h_wins=h2h_losses_a, h2h_losses=h2h_wins_a, matches_last_7d=b_recent, days_since_last_match=b_days_since),
    )


def h2h_adjusted_probability(raw_probability: float, form: MatchupForm) -> float:
    """Appka appčin Elo odhad posune směrem k H2H empirickému poměru
    výher player_a — s váhou rostoucí podle počtu vzájemných zápasů,
    shora omezenou H2H_MAX_WEIGHT (viz docstring modulu)."""
    n = form.player_a.h2h_total
    if n == 0:
        return raw_probability
    h2h_rate = form.player_a.h2h_wins / n
    weight = min(n / (n + H2H_HALF_LIFE_MATCHES), H2H_MAX_WEIGHT)
    return (1 - weight) * raw_probability + weight * h2h_rate


def fatigue_elo_adjustment(form: MatchupForm) -> float:
    """Appka appce vrátí Elo-ekvivalentní posun appka přičte k appčinu
    blended_elo player_a PŘED win_probability (kladné číslo appku
    zvýhodní, záporné appku znevýhodní) — podle rozdílu odehraných
    zápasů za posledních RECENT_WINDOW_DAYS dní."""
    diff = form.player_b.matches_last_7d - form.player_a.matches_last_7d
    return diff * FATIGUE_ELO_PER_EXTRA_MATCH


def layoff_confidence_multiplier(form: MatchupForm) -> float:
    """Appka appce vrátí násobitel appka aplikuje na appčinu
    combined_confidence (1.0 = appka nic nemění) — appka ho sníží za
    KAŽDÉHO hráče s dlouhou pauzou (LONG_LAYOFF_DAYS+ dní bez zápasu)."""
    multiplier = 1.0
    for signal in (form.player_a, form.player_b):
        if signal.days_since_last_match is not None and signal.days_since_last_match >= LONG_LAYOFF_DAYS:
            multiplier *= LAYOFF_CONFIDENCE_MULTIPLIER
    return multiplier
