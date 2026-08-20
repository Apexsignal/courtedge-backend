"""
api_tennis_provider.py — klient pro api-tennis.com.

**Appka zjistila, že tenhle zdroj řeší OBĚ appčiny největší otevřené
otázky najednou** (viz README, sekce o datech):

1. **Historická data pro Elo/ace-rate.** Na rozdíl od Sackmann/TML
   zdrojů je api-tennis.com PLACENÁ komerční API služba (ne akademický
   dataset pod CC-NonCommercial) — appka na ni proto nemá stejný
   licenční problém. Terms of Use appka přečetla a jsou k komerčnímu
   použití mlčenlivé (nejsou tam ani explicitní povolení, ani zákaz),
   ale celý byznys model služby (placené tiery pro "applications and
   individual developers", $40–120/měsíc) komerční nasazení předpokládá
   — na rozdíl od akademického datasetu, kde bylo použití appky (placené
   předplatné) výslovně zakázané. Appka doporučuje při větším provozu
   vyžádat od api-tennis.com podpory písemné potvrzení, ale jako
   startovní bod appka tenhle zdroj používá.
2. **Settlement.** `get_fixtures` appce vrátí `event_status` přímo jako
   `Finished` / `Retired` / `Walk Over` / `Cancelled`, se statistikami
   (esa, gemy) rovnou v odpovědi — appka nemusí čekat na zpožděný
   dataset ani nic dopočítávat (viz `api_tennis_ingest.py`).

**Appka NEMÁ trvalý free tier** (ověřeno: $40–120/měsíc, jen 14denní
trial) — appka potřebuje platný `APITENNIS_KEY` (env var, viz README).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

BASE_URL = "https://api.api-tennis.com/tennis/"
REQUEST_TIMEOUT = 30

EVENT_TYPE_ATP_SINGLES = 265
EVENT_TYPE_WTA_SINGLES = 266
EVENT_TYPE_CHALLENGER_MEN_SINGLES = 281
EVENT_TYPE_CHALLENGER_WOMEN_SINGLES = 272
# Appka 2026-08-20 přidala Challenger vedle hlavního touru — STEJNÝ
# hráčský pool (`tour` zůstává jen 'atp'/'wta', appka nezavádí nový
# rozměr do schématu). Challenger appce dává víc zápasů pro appčino
# Elo, hlavně na turnajích jako dnešní Cincinnati čtvrtfinále, kde má
# appka na hlavním touru jen pár zápasů denně (viz README, "Challenger
# jako doplněk hlavního touru"). Appka fixture appce označí přes
# `event_type_type` v odpovědi (`fixture_to_raw_match` z toho appce
# odvodí `tourney_level='C'` pro nižší K-faktor, viz elo_model.py).
EVENT_TYPE_BY_TOUR = {
    "atp": [EVENT_TYPE_ATP_SINGLES, EVENT_TYPE_CHALLENGER_MEN_SINGLES],
    "wta": [EVENT_TYPE_WTA_SINGLES, EVENT_TYPE_CHALLENGER_WOMEN_SINGLES],
}

# api-tennis.com appce vrací klíč "tournament_sourface" (překlep v jejich
# API, appka ho tady jen citovala), s variantami appka musí normalizovat
# ("Hard (Indoor)", prázdné stringy, i pár nesmyslných hodnot jako
# "- Promotion" — appka to zjistila při ověřování `get_tournaments`).
SURFACE_NORMALIZE = {
    "hard": "hard", "hard (indoor)": "hard",
    "clay": "clay", "clay (indoor)": "clay",
    "grass": "grass", "grass (indoor)": "grass",
    "carpet": "carpet", "carpet (indoor)": "carpet",
}
FINISHED_STATUSES = {"Finished", "Retired", "Walk Over"}
RETIREMENT_STATUSES = {"Retired", "Walk Over"}


def _api_key() -> str:
    key = os.environ.get("APITENNIS_KEY", "")
    if not key:
        raise RuntimeError("APITENNIS_KEY není nastavená.")
    return key


def _call(method: str, **params):
    """Appka vrací syrové `result` appky api-tennis.com — u většiny
    metod je to list (get_fixtures, get_tournaments...), u `get_odds`
    je to dict klíčovaný podle `event_key` (appka to nesjednocuje,
    jednotlivé `get_*` wrappery níže appce dávají přesný typ)."""
    query = {"method": method, "APIkey": _api_key(), **params}
    resp = requests.get(BASE_URL, params=query, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success") != 1:
        raise RuntimeError(f"api-tennis.com vrátila chybu pro {method}: {data}")
    return data.get("result", [])


def get_fixtures(date_start: str, date_stop: str, tour: str) -> list[dict]:
    """date_start/date_stop appka posílá jako 'YYYY-MM-DD'. Appka bere
    jen singles (appčiny 3 trhy jsou jen singles, žádný debl). Appka
    volá zvlášť pro hlavní tour a pro Challenger (api-tennis.com bere
    jen jeden event_type_key na volání) a výsledky spojí — dedupe podle
    event_key appka nepotřebuje, úrovně se navzájem nepřekrývají."""
    matches: list[dict] = []
    for event_type_key in EVENT_TYPE_BY_TOUR[tour]:
        matches.extend(_call(
            "get_fixtures",
            date_start=date_start, date_stop=date_stop,
            event_type_key=event_type_key,
        ))
    return matches


def get_odds(date_start: str, date_stop: str, tour: str) -> dict[str, dict]:
    """Appka vrací {event_key (jako string): {market_name: {...}}} —
    appka bere kurzy od ~12 bookmakerů najednou (viz api_tennis_sync.py,
    appka si z nich vybírá nejlepší cenu). Appka sloučí hlavní tour
    i Challenger stejně jako u `get_fixtures` výš."""
    merged: dict[str, dict] = {}
    for event_type_key in EVENT_TYPE_BY_TOUR[tour]:
        raw = _call(
            "get_odds",
            date_start=date_start, date_stop=date_stop,
            event_type_key=event_type_key,
        )
        if isinstance(raw, dict):
            merged.update(raw)
    return merged


def get_h2h(first_player_key: str, second_player_key: str) -> dict:
    """Appka vrátí {"H2H": [...], "firstPlayerResults": [...],
    "secondPlayerResults": [...]} — jedno volání appce dá VZÁJEMNOU
    historii obou hráčů A poslední zápasy KAŽDÉHO z nich zvlášť (appka
    z toho počítá H2H poměr i signál únavy/odpočinku, viz head_to_head.py)."""
    raw = _call("get_H2H", first_player_key=first_player_key, second_player_key=second_player_key)
    return raw if isinstance(raw, dict) else {}


def normalize_surface(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return SURFACE_NORMALIZE.get(raw.strip().lower())


def get_surface_by_tournament(tour: Optional[str] = None) -> dict[int, Optional[str]]:
    """Appka vrátí {tournament_key: 'hard'|'clay'|'grass'|'carpet'|None}."""
    raw = _call("get_tournaments")
    allowed_types = set(EVENT_TYPE_BY_TOUR.get(tour, [])) if tour else None
    result = {}
    for t in raw:
        if allowed_types is not None and t.get("event_type_key") not in allowed_types:
            continue
        result[t["tournament_key"]] = normalize_surface(t.get("tournament_sourface"))
    return result


# ------------------------------------------------------------
# `statistics` appka dostává v každém fixture jako plochý seznam
# {player_key, stat_period, stat_type, stat_name, stat_value, stat_won,
# stat_total} — appka tyhle helpery sdílí mezi `api_tennis_ingest.py`
# (historický přepočet Elo/ace-rate) a `api_tennis_sync.py` (settlement),
# ať appka logiku "najdi mi tuhle statistiku hráče" nemá na dvou místech.
# ------------------------------------------------------------
def find_match_stat(statistics: list[dict], player_key, stat_name: str, period: str = "match") -> Optional[dict]:
    for s in statistics:
        if s.get("player_key") == player_key and s.get("stat_period") == period and s.get("stat_name") == stat_name:
            return s
    return None


def match_stat_value_int(statistics: list[dict], player_key, stat_name: str) -> Optional[int]:
    s = find_match_stat(statistics, player_key, stat_name)
    if s is None or s.get("stat_value") is None:
        return None
    try:
        return int(s["stat_value"])
    except (TypeError, ValueError):
        return None


def match_stat_total(statistics: list[dict], player_key, stat_name: str) -> Optional[int]:
    s = find_match_stat(statistics, player_key, stat_name)
    return s.get("stat_total") if s else None
