"""
settlement.py — vyhodnocení tiketů po odehrání zápasů.

**OTEVŘENÁ OTÁZKA (viz README):** appka zatím nemá automatizovaný zdroj
výsledků zápasů (vítěz, total_games, total_aces) hned po skončení —
Jeff Sackmann / TML-Database datasety appka aktualizují se zpožděním
(dny), the-odds-api dává jen kurzy PŘED zápasem, ne výsledky po něm.
Appka proto zatím vyhodnocuje jen zápasy, které má appka RUČNĚ zapsané
přes `POST /admin/match-result` (viz backend_api.py) — plnou automatizaci
appka doplní, až se vybere zdroj live/final skóre.

Appka VOIDuje leg na total_games/total_aces, pokud zápas skončil
skrečem (appka nemůže spravedlivě vyhodnotit hranici gemů/es u zápasu,
který se nedohrál) — leg na výherce appka i tak vyhodnotí normálně
(soupeř skrečujícího hráče vyhrál).
"""
from __future__ import annotations

import db


def _settle_leg_result(leg: dict) -> str:
    """Appka vrátí 'won' | 'lost' | 'void' pro jeden leg dohraného zápasu."""
    market = leg["market_code"]

    if market == "match_winner":
        winner_id = leg["winner_id"]
        picked_id = leg["player_a_id"] if leg["selection"] == "player_a" else leg["player_b_id"]
        return "won" if winner_id == picked_id else "lost"

    if leg["match_retirement"]:
        return "void"

    if market == "total_games":
        total = leg["match_total_games"]
    elif market == "total_aces":
        total = leg["match_total_aces"]
    else:
        return "void"

    if total is None or leg["line"] is None:
        return "void"

    over_won = total > float(leg["line"])
    if leg["selection"] == "over":
        return "won" if over_won else "lost"
    return "lost" if over_won else "won"


def settle_ticket(ticket_id: int) -> str | None:
    """Appka vrátí výsledný status tiketu, nebo None, pokud ještě není
    co vyhodnotit (aspoň jeden zápas nemá zapsaný výsledek)."""
    legs = db.get_ticket_legs(ticket_id)
    if not legs:
        return None
    if any(leg["match_status"] != "finished" for leg in legs):
        return None  # appka čeká, dokud NEjsou hotové VŠECHNY zápasy tiketu

    results = []
    for leg in legs:
        result = _settle_leg_result(leg)
        db.set_leg_result(leg["id"], result)
        results.append(result)

    if "lost" in results:
        ticket_status = "lost"
    elif all(r == "void" for r in results):
        ticket_status = "void"
    else:
        ticket_status = "won"  # zbylé legy jsou 'won' nebo 'void', žádný 'lost'

    db.set_ticket_status(ticket_id, ticket_status)
    return ticket_status


def settle_all_pending() -> dict[str, int]:
    """Appka projde VŠECHNY pending tikety a zkusí je vyhodnotit —
    stejný princip jako ApexSignalovo `/admin/settle-all-pending`."""
    summary = {"won": 0, "lost": 0, "void": 0, "still_pending": 0}
    for ticket in db.get_pending_tickets():
        status = settle_ticket(ticket["id"])
        if status is None:
            summary["still_pending"] += 1
        else:
            summary[status] += 1
    return summary
