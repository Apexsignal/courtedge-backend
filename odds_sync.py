"""
odds_sync.py — appka stáhne tenisové kurzy z the-odds-api a spáruje je
s vlastní `matches`/`players` tabulkou.

**Známé omezení appky:** the-odds-api nevrací POVRCH kurtu (surface) —
Elo/market modely na povrchu ale staví (viz elo_model.py, market_models.py).
Appka proto povrch odhaduje z názvu turnaje přes `TOURNEY_SURFACE_HINTS`
(pokrývá jen velké/známé turnaje) a jinak defaultuje na `hard` (většina
ATP/WTA turnajů v sezóně JE na tvrdém povrchu, takže je to rozumný
fallback, ne náhodná volba) — ale appka by měla mít způsob, jak si
admin surface u konkrétního zápasu ručně opraví, než appka zápas
zahrne do generování tiketu (viz `PATCH /admin/matches/{id}` v
backend_api.py).

Appka NEVYTVÁŘÍ hráče za běhu — pokud jméno z the-odds-api nenajde v DB
(přes `db.find_player_by_name`), appka zápas přeskočí. Hráč musí existovat
z historického importu (viz data_ingest.py), jinak appka nemá Elo/ace
rate, ze kterých by počítala pravděpodobnost.
"""
from __future__ import annotations

import db
import odds_provider

TOURNEY_SURFACE_HINTS = {
    "wimbledon": "grass",
    "french open": "clay",
    "roland garros": "clay",
    "australian open": "hard",
    "us open": "hard",
    "monte carlo": "clay",
    "madrid": "clay",
    "rome": "clay",
    "italia": "clay",
    "hamburg": "clay",
    "queen's": "grass",
    "halle": "grass",
    "s-hertogenbosch": "grass",
    "eastbourne": "grass",
}
DEFAULT_SURFACE = "hard"


def _guess_surface(tourney_name: str) -> str:
    name_lower = (tourney_name or "").lower()
    for hint, surface in TOURNEY_SURFACE_HINTS.items():
        if hint in name_lower:
            return surface
    return DEFAULT_SURFACE


def _tour_from_sport_key(sport_key: str) -> str:
    return "wta" if "wta" in sport_key else "atp"


def sync_upcoming_matches() -> dict[str, int]:
    """Appka projde aktuální the-odds-api eventy, spáruje hráče a
    zápasy uloží/aktualizuje včetně kurzů. Vrací počty pro admin přehled."""
    summary = {"events_seen": 0, "matches_synced": 0, "matches_skipped_unknown_player": 0, "odds_saved": 0}

    for event in odds_provider.fetch_all_tennis_events():
        summary["events_seen"] += 1
        sport_key = event.get("sport_key", "")
        tour = _tour_from_sport_key(sport_key)
        player_a_name = event.get("home_team")
        player_b_name = event.get("away_team")
        if not player_a_name or not player_b_name:
            continue

        player_a = db.find_player_by_name(player_a_name, tour)
        player_b = db.find_player_by_name(player_b_name, tour)
        if not player_a or not player_b:
            summary["matches_skipped_unknown_player"] += 1
            continue

        tourney_name = event.get("sport_title", sport_key)
        match = db.upsert_upcoming_match({
            "external_id": event["id"],
            "tour": tour,
            "tourney_name": tourney_name,
            "surface": _guess_surface(tourney_name),
            "tourney_level": None,
            "round": None,
            "best_of": 3,
            "start_time": event.get("commence_time"),
            "player_a_id": player_a["id"],
            "player_b_id": player_b["id"],
        })
        summary["matches_synced"] += 1

        winner_odds = odds_provider.extract_match_winner_odds(event)
        for name, price in winner_odds.items():
            if name == player_a_name:
                db.save_odds_snapshot(match["id"], "match_winner", "player_a", None, "the-odds-api", price)
                summary["odds_saved"] += 1
            elif name == player_b_name:
                db.save_odds_snapshot(match["id"], "match_winner", "player_b", None, "the-odds-api", price)
                summary["odds_saved"] += 1

        for line_entry in odds_provider.extract_total_games_odds(event):
            line = line_entry.get("line")
            if line is None:
                continue
            if line_entry.get("over_odds"):
                db.save_odds_snapshot(match["id"], "total_games", "over", line, "the-odds-api", line_entry["over_odds"])
                summary["odds_saved"] += 1
            if line_entry.get("under_odds"):
                db.save_odds_snapshot(match["id"], "total_games", "under", line, "the-odds-api", line_entry["under_odds"])
                summary["odds_saved"] += 1

    return summary
