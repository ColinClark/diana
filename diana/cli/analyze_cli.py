#!/usr/bin/env python3
"""
Command-line interface for analyzing posteriors.
"""

import sys
import argparse
import itertools
import pandas as pd
from scipy.stats import beta

def credible_interval(a, b, level=0.95):
    """
    Calculate credible interval for Beta distribution.
    
    Args:
        a: Alpha parameter
        b: Beta parameter
        level: Credible interval level (0-1)
        
    Returns:
        tuple: (lower bound, upper bound)
    """
    lo, hi = beta.ppf([(1-level)/2, 1-(1-level)/2], a, b)
    return lo, hi

def prob_superiority(a1, b1, a2, b2, draws=50_000):
    """
    Calculate probability that variant 1 is superior to variant 2.
    
    Uses Monte Carlo sampling from Beta distributions.
    
    Args:
        a1: Alpha parameter for variant 1
        b1: Beta parameter for variant 1
        a2: Alpha parameter for variant 2
        b2: Beta parameter for variant 2
        draws: Number of Monte Carlo samples
        
    Returns:
        float: Probability of superiority (0-1)
    """
    # Monte‑Carlo Thompson draws
    p1 = beta.rvs(a1, b1, size=draws)
    p2 = beta.rvs(a2, b2, size=draws)
    return (p1 > p2).mean()

def main():
    """
    Main entry point for the analyze posteriors CLI.
    """
    ap = argparse.ArgumentParser(description="Analyze Bayesian A/B test results")
    ap.add_argument("csv", help="CSV produced by the engine")
    ap.add_argument("--level", type=float, default=0.95,
                   help="Credible interval level (default: 0.95)")
    ap.add_argument("--draws", type=int, default=50000,
                   help="Monte Carlo sample size (default: 50,000)")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.csv)
    except Exception as e:
        print(f"Error reading CSV file: {e}", file=sys.stderr)
        return 1

    for test_id, grp in df.groupby("test_id"):
        # keep the most recent row per variant
        latest = grp.sort_values("timestamp").groupby("variant").tail(1)

        print(f"\n=== {test_id}  (n_variants={len(latest)}) ===")
        stats = {}

        for _, row in latest.iterrows():
            v, a, b = row["variant"], row["alpha"], row["beta"]
            mean = a / (a + b)
            lo, hi = credible_interval(a, b, args.level)
            stats[v] = (a, b)          # save for pair‑wise comps
            print(f"{v:>10}:  mean={mean:.4f}   {args.level*100:.0f}% CI=({lo:.4f}, {hi:.4f})   "
                  f"α={int(a):>4}  β={int(b):>4}")

        # pairwise probability of being better
        print("  • Probability of superiority:")
        for v1, v2 in itertools.combinations(stats.keys(), 2):
            a1, b1 = stats[v1]
            a2, b2 = stats[v2]
            p = prob_superiority(a1, b1, a2, b2, args.draws)
            better = v1 if p >= 0.5 else v2
            print(f"    {v1} vs {v2}:  "
                  f"P({v1} > {v2}) = {p:.3f}   ⇒ likely winner: {better}")
    
    return 0

if __name__ == "__main__":
    exit(main())