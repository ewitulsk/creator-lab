#!/usr/bin/env python3
"""Look for a fixed cycle in a TRIBE prediction array (rows = timesteps).

    python scripts/tribe_periodicity.py preds.npy [--period 120] [--max-lag 200]

Prints the mean correlation between rows t and t+lag for each lag, and the fraction of
variance explained by a cycle of the given period (the per-phase means of t mod period).
See docs/tribe-timing.md.
"""

from __future__ import annotations

import argparse

import numpy as np


def lag_similarity(preds: np.ndarray, max_lag: int) -> np.ndarray:
    """Mean Pearson correlation between rows t and t+lag (rows z-scored across vertices)."""
    x = preds[~np.isnan(preds).any(axis=1)].astype(np.float64)
    x = (x - x.mean(axis=1, keepdims=True)) / (x.std(axis=1, keepdims=True) + 1e-12)
    out = np.full(max_lag + 1, np.nan)
    for lag in range(1, min(max_lag, len(x) - 1) + 1):
        out[lag] = float(np.mean(np.sum(x[:-lag] * x[lag:], axis=1) / x.shape[1]))
    return out


def periodic_variance_fraction(preds: np.ndarray, period: int) -> float:
    """Share of the (time-centred) variance explained by per-phase means of t mod period."""
    x = preds[~np.isnan(preds).any(axis=1)].astype(np.float64)
    x = x - x.mean(axis=0, keepdims=True)
    phase = np.arange(len(x)) % period
    fitted = np.zeros_like(x)
    for p in range(period):
        mask = phase == p
        if mask.sum() > 1:
            fitted[mask] = x[mask].mean(axis=0)
    total = float((x**2).sum())
    return float((fitted**2).sum() / total) if total else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("preds")
    ap.add_argument("--period", type=int, default=120)
    ap.add_argument("--max-lag", type=int, default=200)
    args = ap.parse_args()
    preds = np.load(args.preds).astype(np.float32)
    print(f"{args.preds}: {preds.shape[0]} rows x {preds.shape[1]} vertices")
    sim = lag_similarity(preds, args.max_lag)
    top = np.argsort(np.nan_to_num(sim[2:], nan=-9))[::-1][:5] + 2
    print("strongest lags (row correlation):", ", ".join(f"{lag}: {sim[lag]:.3f}" for lag in top))
    print(f"lag {args.period}: {sim[args.period]:.3f}" if args.period <= args.max_lag else "")
    frac = periodic_variance_fraction(preds, args.period)
    # A cycle-free series still gets ~period/rows explained by chance; report it alongside.
    chance = args.period / max(len(preds), 1)
    print(f"variance explained by a {args.period}-row cycle: {frac:.1%} (chance level about {chance:.1%})")


if __name__ == "__main__":
    main()
