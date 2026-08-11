#!/usr/bin/env python3
"""
scripts/analyze_backtest.py — appka tady vezme syrové (predikce, realita)
páry z backtest_calibration.py a řekne appce, jestli appčiny modely fakt
sedí na realitě, a o kolik appka musí přeladit appčiny konstanty
(GAMES_STD_DEV, GAMES_ELO_GAP_SENSITIVITY, BASELINE_GAMES) v
market_models.py.

Použití:
    python3 scripts/analyze_backtest.py data/backtest/atp_backtest.json data/backtest/wta_backtest.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def ols_slope_intercept(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Appka počítá obyčejnou lineární regresi (nejmenší čtverce) ručně,
    appka nechce kvůli tomuhle přidávat numpy jako závislost appky."""
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    return slope, intercept


def analyze_win(records: list[dict]) -> None:
    print(f"\n=== VÝHERCE — {len(records)} zápasů ===")
    if not records:
        print("  (appka nemá dost dat)")
        return
    brier = mean([(r["p_a"] - (1.0 if r["a_won"] else 0.0)) ** 2 for r in records])
    print(f"  Brier score: {brier:.4f}  (appka chce co nejblíž 0; 0.25 appka bere jako 'stejně dobré jako hodit mincí')")

    buckets = defaultdict(list)
    for r in records:
        bucket = round(r["p_a"] * 10) / 10  # appka bucketuje po 0.1 (50%, 60%, 70%...)
        buckets[bucket].append(r["a_won"])
    print("  Kalibrace (appčina predikovaná jistota vs. skutečná úspěšnost):")
    print("  predikce | appka měla N zápasů | doopravdy vyhráno")
    for bucket in sorted(buckets):
        outcomes = buckets[bucket]
        if len(outcomes) < 15:
            continue
        actual_rate = sum(outcomes) / len(outcomes)
        flag = "  ⚠️ appka nadhodnocuje" if actual_rate < bucket - 0.07 else ("  ⚠️ appka podhodnocuje" if actual_rate > bucket + 0.07 else "")
        print(f"  {bucket:.0%}      | {len(outcomes):5d}              | {actual_rate:.1%}{flag}")


def analyze_games(records: list[dict]) -> None:
    print(f"\n=== GEMY — {len(records)} zápasů ===")
    if not records:
        print("  (appka nemá dost dat)")
        return
    residuals = [r["actual_games"] - r["expected_games"] for r in records]
    bias = mean(residuals)
    real_std = stdev(residuals)
    print(f"  appčin odhad má systematickou odchylku (bias): {bias:+.2f} gemu  (appka chce co nejblíž 0)")
    print(f"  appka má natvrdo GAMES_STD_DEV = 4.2, appka ze skutečných dat naměřila: {real_std:.2f}")

    gaps = [r["elo_gap"] for r in records]
    actuals = [r["actual_games"] for r in records]
    slope, intercept = ols_slope_intercept(gaps, actuals)
    print(f"  appka má natvrdo GAMES_ELO_GAP_SENSITIVITY = -0.01, appka ze skutečných dat naměřila sklon: {slope:.4f}")
    print(f"  appka má natvrdo baseline (bez ohledu na gap) 22.0 gemu, appka naměřila (regresní intercept): {intercept:.2f}")


def analyze_aces(records: list[dict]) -> None:
    print(f"\n=== ESA — {len(records)} zápasů ===")
    if not records:
        print("  (appka nemá dost dat)")
        return
    residuals = [r["actual_aces"] - r["expected_aces"] for r in records]
    bias = mean(residuals)
    actuals = [r["actual_aces"] for r in records]
    real_mean = mean(actuals)
    real_var = stdev(actuals) ** 2
    dispersion = real_var / real_mean if real_mean else float("nan")
    print(f"  appčin odhad má systematickou odchylku (bias): {bias:+.2f} esa  (appka chce co nejblíž 0)")
    print(f"  appka počítá s Poissonovým rozdělením (appka předpokládá rozptyl == průměr, poměr 1.0)")
    print(f"  appka ze skutečných dat naměřila poměr rozptyl/průměr: {dispersion:.2f}", end="")
    if dispersion > 1.3:
        print("  ⚠️ esa appka mají VĚTŠÍ rozptyl, než Poisson předpokládá — appčiny extrémní jistoty (95 %+) appka nemá brát doslova")
    else:
        print("  (appka bere blízko 1.0 jako v pořádku)")


def main() -> None:
    if len(sys.argv) < 2:
        print("použití: python3 scripts/analyze_backtest.py <backtest1.json> [backtest2.json ...]", file=sys.stderr)
        sys.exit(1)

    all_win, all_games, all_aces = [], [], []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        print(f"--- {path} ({d['tour']}) ---")
        print(f"  {len(d['win'])} win / {len(d['games'])} games / {len(d['aces'])} aces záznamů")
        all_win.extend(d["win"])
        all_games.extend(d["games"])
        all_aces.extend(d["aces"])

    print("\n" + "=" * 60)
    print("SOUHRNNÁ KALIBRACE (appka spojila všechny zdroje dohromady)")
    print("=" * 60)
    analyze_win(all_win)
    analyze_games(all_games)
    analyze_aces(all_aces)


if __name__ == "__main__":
    main()
