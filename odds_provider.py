"""
odds_provider.py — klient pro the-odds-api.com, tenisové trhy.

VLASTNÍ klíč appky (`ODDSAPI_KEY`), NE sdílený s ApexSignalem — appka má
plně oddělenou infrastrukturu (viz README).

Důležité odlišnosti tenisu na the-odds-api oproti fotbalu:
- Appka NEMÁ jeden univerzální "tennis" sport_key. Každý turnaj má
  vlastní klíč (např. `tennis_atp_wimbledon`, `tennis_wta_us_open`) a
  appka dostane data jen pro turnaje, které právě PROBÍHAJÍ (in season).
  Appka proto musí nejdřív zavolat `/v4/sports` a filtrovat klíče
  začínající `tennis_`, ne si je natvrdo hardcodovat.
- Trhy appka mapuje na svoje tři:
    match_winner → `h2h`
    total_games  → `totals` (games)
  `total_aces` (esa) **the-odds-api NENABÍZÍ jako bookmaker trh** — appka
  na něj nemá tržní kurz, jen svůj vlastní odhad (viz market_models.py).
  ticket_builder.py s tím počítá (`market_odds=None` u esového legu appku
  z výběru pro ODDS ticketu vyřadí, pokud appka nemá jiný zdroj kurzu —
  do doby, než appka najde broker/API s ace-totals trhem, appka může
  esový trh nabízet jen jako informativní tip bez kurzu, ne jako
  ticket leg).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
REQUEST_TIMEOUT = 15


def _api_key() -> str:
    key = os.environ.get("ODDSAPI_KEY", "")
    if not key:
        raise RuntimeError("ODDSAPI_KEY není nastavená (vlastní klíč appky, ne ApexSignalu).")
    return key


def list_tennis_sport_keys() -> list[str]:
    """Appka zjistí, které tenisové turnaje the-odds-api PRÁVĚ nabízí
    (in season) — sport_key appka nesmí hardcodovat, mění se sezónu od sezóny."""
    resp = requests.get(
        f"{BASE_URL}/sports",
        params={"apiKey": _api_key()},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return [s["key"] for s in resp.json() if s.get("key", "").startswith("tennis_")]


def fetch_odds_for_tournament(sport_key: str, markets: str = "h2h,totals") -> list[dict]:
    """Appka vrátí syrovou odpověď the-odds-api pro jeden turnajový sport_key."""
    resp = requests.get(
        f"{BASE_URL}/sports/{sport_key}/odds",
        params={
            "apiKey": _api_key(),
            "regions": "eu",
            "markets": markets,
            "oddsFormat": "decimal",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def extract_match_winner_odds(event: dict) -> dict[str, float]:
    """Appka vezme první bookmaker's h2h nabídku a vrátí {hráč_jméno: kurz}.
    Víc bookmakerů appka zatím neagreguje (best-of) — jednoduchý start,
    vylepšit až appka bude mít reálná data k porovnání."""
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") == "h2h":
                return {o["name"]: o["price"] for o in market.get("outcomes", [])}
    return {}


def extract_total_games_odds(event: dict) -> list[dict]:
    """Appka vrátí seznam {line, over_odds, under_odds} pro trh total_games
    (the-odds-api ho posílá jako `totals`, appka to mapuje 1:1)."""
    results: list[dict] = []
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "totals":
                continue
            by_line: dict[float, dict] = {}
            for outcome in market.get("outcomes", []):
                line = outcome.get("point")
                if line is None:
                    continue
                entry = by_line.setdefault(line, {"line": line})
                if outcome.get("name") == "Over":
                    entry["over_odds"] = outcome.get("price")
                elif outcome.get("name") == "Under":
                    entry["under_odds"] = outcome.get("price")
            results.extend(by_line.values())
        if results:
            break  # appka bere první bookmaker s totals trhem, viz pozn. výše u h2h
    return results


def fetch_all_tennis_events() -> list[dict]:
    """Appka projde všechny aktuálně nabízené tenisové turnaje a vrátí
    společný seznam eventů (appka je pak spáruje na vlastní `matches`
    tabulku přes jména hráčů/čas začátku — the-odds-api nemá appčino
    interní ID)."""
    events: list[dict] = []
    for sport_key in list_tennis_sport_keys():
        try:
            events.extend(fetch_odds_for_tournament(sport_key))
        except requests.HTTPError:
            continue  # appka jeden nedostupný turnaj přeskočí, neshodí celý fetch
    return events
