#!/usr/bin/env python3
"""
Command-line interface for analyzing posteriors.
"""

import sys
import argparse
import itertools
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import beta
from datetime import datetime

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

def print_summary_stats(df):
    """
    Print summary statistics about the dataset.
    
    Args:
        df: DataFrame with posterior data
    """
    print("=== Dataset Summary ===")
    print(f"Total records: {len(df):,}")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Duration: {df['timestamp'].max() - df['timestamp'].min()}")
    print(f"Tests: {', '.join(df['test_id'].unique())}")
    print(f"Variants: {', '.join(df['variant'].unique())}")
    
    # Data quality metrics
    print("\n=== Data Quality ===")
    records_per_test = df.groupby('test_id').size()
    records_per_variant = df.groupby(['test_id', 'variant']).size()
    
    print(f"Records per test: {records_per_test.to_dict()}")
    print(f"Records per variant: {dict(records_per_variant)}")
    
    # Missing data check
    missing_data = df.isnull().sum()
    if missing_data.any():
        print(f"Missing data: {missing_data[missing_data > 0].to_dict()}")
    else:
        print("No missing data detected")
    
    # Conversion rate evolution
    print("\n=== Latest Conversion Rates ===")
    latest_data = df.groupby(['test_id', 'variant']).tail(1)
    for _, row in latest_data.iterrows():
        conversion_rate = row['alpha'] / (row['alpha'] + row['beta'])
        total_exposures = row['alpha'] + row['beta'] - 1  # Subtract prior
        print(f"[{row['test_id']}] {row['variant']}: {conversion_rate:.3f} "
              f"({row['alpha']-1:,} conversions / {total_exposures:,} exposures)")

def generate_plots(df, test_id):
    """
    Generate visualization plots for the test results.
    
    Args:
        df: DataFrame with posterior data
        test_id: Test ID to generate plots for
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        
        test_data = df[df['test_id'] == test_id].copy()
        test_data = test_data.sort_values('timestamp')
        
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle(f'Bayesian A/B Test Analysis\nTest ID: {test_id}', fontsize=16)
        
        # Plot 1: Conversion rate evolution
        for variant in test_data['variant'].unique():
            variant_data = test_data[test_data['variant'] == variant]
            conversion_rates = variant_data['alpha'] / (variant_data['alpha'] + variant_data['beta'])
            ax1.plot(variant_data['timestamp'], conversion_rates, 
                    label=f'{variant}', marker='o', alpha=0.7)
        
        ax1.set_title('Conversion Rate Evolution')
        ax1.set_ylabel('Conversion Rate')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        
        # Plot 2: Credible intervals
        latest_data = test_data.groupby('variant').tail(1)
        variants = []
        means = []
        lower_bounds = []
        upper_bounds = []
        
        for _, row in latest_data.iterrows():
            a, b = row['alpha'], row['beta']
            mean = a / (a + b)
            lo, hi = credible_interval(a, b, 0.95)
            
            variants.append(row['variant'])
            means.append(mean)
            lower_bounds.append(lo)
            upper_bounds.append(hi)
        
        y_pos = np.arange(len(variants))
        ax2.barh(y_pos, means, xerr=[np.array(means) - np.array(lower_bounds),
                                   np.array(upper_bounds) - np.array(means)], 
                capsize=5, alpha=0.7)
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(variants)
        ax2.set_title('95% Credible Intervals (Latest)')
        ax2.set_xlabel('Conversion Rate')
        ax2.grid(True, alpha=0.3)
        
        # Plot 3: Sample size evolution
        for variant in test_data['variant'].unique():
            variant_data = test_data[test_data['variant'] == variant]
            sample_sizes = variant_data['alpha'] + variant_data['beta'] - 1
            ax3.plot(variant_data['timestamp'], sample_sizes, 
                    label=f'{variant}', marker='o', alpha=0.7)
        
        ax3.set_title('Sample Size Evolution')
        ax3.set_ylabel('Total Exposures')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        
        # Plot 4: Posterior distributions (latest)
        x = np.linspace(0, 1, 1000)
        for _, row in latest_data.iterrows():
            a, b = row['alpha'], row['beta']
            y = beta.pdf(x, a, b)
            ax4.plot(x, y, label=f"{row['variant']} (α={a:.0f}, β={b:.0f})", alpha=0.7)
        
        ax4.set_title('Current Posterior Distributions')
        ax4.set_xlabel('Conversion Rate')
        ax4.set_ylabel('Density')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save plot
        plot_filename = f"{test_id}_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(plot_filename, dpi=300, bbox_inches='tight')
        print(f"[PLOT] Plot saved: {plot_filename}")
        
        # Try to show plot (works in interactive environments)
        try:
            plt.show()
        except:
            pass  # Fail silently if display not available
            
    except ImportError:
        print("[PLOT] Plotting requires matplotlib. Install with: pip install matplotlib")
    except Exception as e:
        print(f"[PLOT] Error generating plots: {e}")

def export_analysis(df, test_id, export_path):
    """
    Export detailed analysis to file.
    
    Args:
        df: DataFrame with posterior data
        test_id: Test ID to export
        export_path: Output file path
    """
    test_data = df[df['test_id'] == test_id].copy()
    
    # Calculate additional metrics
    test_data['conversion_rate'] = test_data['alpha'] / (test_data['alpha'] + test_data['beta'])
    test_data['total_exposures'] = test_data['alpha'] + test_data['beta'] - 1
    test_data['conversions'] = test_data['alpha'] - 1
    
    # Add credible intervals
    intervals = test_data.apply(lambda row: credible_interval(row['alpha'], row['beta']), axis=1)
    test_data['ci_lower'] = [interval[0] for interval in intervals]
    test_data['ci_upper'] = [interval[1] for interval in intervals]
    
    try:
        if export_path.endswith('.json'):
            test_data.to_json(export_path, orient='records', date_format='iso', indent=2)
        else:
            test_data.to_csv(export_path, index=False)
        
        print(f"[EXPORT] Analysis exported: {export_path}")
    except Exception as e:
        print(f"[EXPORT] Error exporting analysis: {e}")

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
    ap.add_argument("--plot", action="store_true",
                   help="Generate plots showing posterior evolution")
    ap.add_argument("--summary", action="store_true", 
                   help="Show summary statistics and data quality metrics")
    ap.add_argument("--export", 
                   help="Export detailed analysis to file (CSV or JSON)")
    args = ap.parse_args()

    try:
        # Try reading with header first
        df = pd.read_csv(args.csv)
        
        # Check if the file has proper column names or just numeric columns
        expected_columns = ['timestamp', 'test_id', 'variant', 'alpha', 'beta']
        if not all(col in df.columns for col in expected_columns):
            # File doesn't have header or has wrong columns, read without header
            df = pd.read_csv(args.csv, header=None, 
                           names=['timestamp', 'test_id', 'variant', 'alpha', 'beta'])
            print(f"[INFO] CSV file missing header, using inferred column names: {list(df.columns)}")
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    except Exception as e:
        print(f"Error reading CSV file: {e}", file=sys.stderr)
        return 1
    
    if args.summary:
        print_summary_stats(df)
        print()

    for test_id, grp in df.groupby("test_id"):
        # keep the most recent row per variant
        latest = grp.sort_values("timestamp").groupby("variant").tail(1)

        print(f"\n{'='*60}")
        print(f"TEST: {test_id} (variants: {len(latest)})")
        print(f"{'='*60}")
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
        
        # Generate plots if requested
        if args.plot:
            generate_plots(df, test_id)
        
        # Export detailed analysis if requested
        if args.export:
            export_analysis(df, test_id, args.export)
    
    return 0

if __name__ == "__main__":
    exit(main())