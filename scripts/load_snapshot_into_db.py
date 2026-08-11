#!/usr/bin/env python3
"""
scripts/load_snapshot_into_db.py — appka nahraje JSON snapshot
vygenerovaný `pull_historical_snapshot.py` do Postgresu (`players`
tabulka). **Nepotřebuje žádný API klíč** — appka z API stáhla data
jednou (viz snapshot skript) a tenhle krok appka může spustit kdykoliv
znovu, i po vypršení api-tennis.com trialu.

Použití:
    python3 scripts/load_snapshot_into_db.py data/snapshots/atp_2026-08-11.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from data_ingest import PlayerRecord


def _to_date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    if len(sys.argv) != 2:
        print("použití: python3 scripts/load_snapshot_into_db.py <snapshot.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    tour = payload["tour"]
    players = payload["players"]
    print(f"[{tour}] nahrávám {len(players)} hráčů ze snapshotu ({payload.get('generated_at')})...")

    for i, p in enumerate(players, start=1):
        record = PlayerRecord(
            external_id=p["external_id"],
            tour=p["tour"],
            full_name=p["full_name"],
            elo_overall=p["elo_overall"],
            elo_hard=p["elo_hard"],
            elo_clay=p["elo_clay"],
            elo_grass=p["elo_grass"],
            elo_carpet=p["elo_carpet"],
            matches_played_total=p["matches_played_total"],
            matches_played_12mo=p["matches_played_12mo"],
            ace_rate_hard=p["ace_rate_hard"],
            ace_rate_clay=p["ace_rate_clay"],
            ace_rate_grass=p["ace_rate_grass"],
            ace_rate_carpet=p["ace_rate_carpet"],
            recent_retirements_12mo=p["recent_retirements_12mo"],
            last_match_date=_to_date(p["last_match_date"]),
        )
        db.upsert_player(record)
        if i % 200 == 0:
            print(f"  ...{i}/{len(players)}")

    print(f"[{tour}] hotovo.")


if __name__ == "__main__":
    main()
