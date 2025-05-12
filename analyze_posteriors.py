#!/usr/bin/env python3
"""
analyze_posteriors.py
–––––––––––––––––––––
Reads a CSV produced by the Bayesian engine (columns: timestamp, test_id,
variant, alpha, beta) and prints, per test:

• Posterior mean
• 95 % credible interval
• Probability each variant is the best (pair‑wise superiority)

Usage
-----
python analyze_posteriors.py posteriors_demo.csv
"""

import sys, argparse, itertools, pandas as pd
from scipy.stats import beta

def credible_interval(a, b, level=0.95):
    lo, hi = beta.ppf([(1-level)/2, 1-(1-level)/2], a, b)
    return lo, hi

def prob_superiority(a1, b1, a2, b2, draws=50_000):
    # Monte‑Carlo Thompson draws
    p1 = beta.rvs(a1, b1, size=draws)
    p2 = beta.rvs(a2, b2, size=draws)
    return (p1 > p2).mean()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="CSV produced by the engine")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)

    for test_id, grp in df.groupby("test_id"):
        # keep the most recent row per variant
        latest = grp.sort_values("timestamp").groupby("variant").tail(1)

        print(f"\n=== {test_id}  (n_variants={len(latest)}) ===")
        stats = {}

        for _, row in latest.iterrows():
            v, a, b = row["variant"], row["alpha"], row["beta"]
            mean = a / (a + b)
            lo, hi = credible_interval(a, b)
            stats[v] = (a, b)          # save for pair‑wise comps
            print(f"{v:>10}:  mean={mean:.4f}   95% CI=({lo:.4f}, {hi:.4f})   "
                  f"α={int(a):>4}  β={int(b):>4}")

        # pairwise probability of being better
        print("  • Probability of superiority:")
        for v1, v2 in itertools.combinations(stats.keys(), 2):
            a1, b1 = stats[v1]
            a2, b2 = stats[v2]
            p = prob_superiority(a1, b1, a2, b2)
            better = v1 if p >= 0.5 else v2
            print(f"    {v1} vs {v2}:  "
                  f"P({v1} > {v2}) = {p:.3f}   ⇒ likely winner: {better}")

if __name__ == "__main__":
    main()

