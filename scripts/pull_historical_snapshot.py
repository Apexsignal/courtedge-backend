#!/usr/bin/env python3
"""
scripts/pull_historical_snapshot.py — appka tímhle stáhne historii
z api-tennis.com JEDNOU a uloží VÝSLEDNÝ přepočet Elo/ace-rate jako
kompaktní JSON snapshot (per-hráč souhrn, ne syrová fixtures data).

**Proč appka tohle dělá zvlášť, ne přímo přes `/admin/ingest-api-tennis`:**
appčin api-tennis.com klíč je zatím na 14denním free trialu (appka to
neověřuje sama — uživatel to potvrdil v chatu) — appka chce z trialu
vytáhnout maximum HNED, uložit výsledek jako soubor nezávislý na dalším
přístupu k API, a teprve POTOM (kdykoliv, i po vypršení trialu) ho
naimportovat do Postgresu přes `scripts/load_snapshot_into_db.py`, který
už žádný API klíč nepotřebuje.

Použití:
    python3 scripts/pull_historical_snapshot.py --tour atp \
        --start 2022-01-01 --end 2026-08-11 \
        --out data/snapshots/atp_2026-08-11.json

Vyžaduje env var APITENNIS_KEY. Appka postupuje po 7denních oknech
(api-tennis.com delší rozsah v jednom volání odmítá, viz
api_tennis_ingest.CHUNK_DAYS) — u víceletého importu appka dělá stovky
requestů, počítej s desítkami minut.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone

# `python3 scripts/pull_historical_snapshot.py` appce nastaví sys.path[0]
# na `scripts/`, ne na kořen repa, kde appka má `api_tennis_ingest.py` —
# appka si kořen repa musí přidat sama, jinak import selže bez ohledu
# na to, z jakého adresáře appka skript spouští.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_tennis_ingest as ingest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tour", required=True, choices=["atp", "wta"])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="cesta k výstupnímu JSON")
    args = parser.parse_args()

    date_start = date.fromisoformat(args.start)
    date_stop = date.fromisoformat(args.end)

    def progress(chunk_start: date, chunk_end: date, matches_so_far: int) -> None:
        print(f"[{args.tour}] {chunk_start}..{chunk_end}: {matches_so_far} zápasů zatím", file=sys.stderr, flush=True)

    raw_matches = ingest.fetch_raw_matches(args.tour, date_start, date_stop, on_progress=progress)
    print(f"[{args.tour}] celkem syrových zápasů: {len(raw_matches)}", file=sys.stderr, flush=True)

    records = ingest.build_player_ratings(raw_matches, tour=args.tour)
    print(f"[{args.tour}] spočítáno hráčů: {len(records)}", file=sys.stderr, flush=True)

    payload = {
        "tour": args.tour,
        "date_start": args.start,
        "date_stop": args.end,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "api-tennis.com",
        "raw_match_count": len(raw_matches),
        "players": [asdict(r) for r in records],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, default=str, indent=2, ensure_ascii=False)

    print(f"[{args.tour}] hotovo, zapsáno do {args.out}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
