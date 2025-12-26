"""
Test script for crypto regime detection and allocation engine.

This script demonstrates the full workflow:
1. Initialize CryptoRegimeDetector
2. Fetch data and detect regimes (CCXT/Kraken)
3. Prepare returns data
4. Run allocation strategy
5. Calculate and display performance metrics
"""

import numpy as np
import pandas as pd
from app.trading.regime_detection import CryptoRegimeDetector
from app.trading.allocation import run_dro_strategy, AllocationStrategy


def main():
    """Run the full test workflow."""
    
    print("=" * 80)
    print("CRYPTO REGIME MODEL & ALLOCATION ENGINE TEST")
    print("=" * 80)
    
    # --- Step 1: Regime Detection ---
    print("\n[Step 1] Initializing Regime Model...")
    model = CryptoRegimeModel(lookback_periods=365 * 15)
    
    print("[Step 2] Fetching crypto data and calculating features...")
    # Note: This will fetch live data from tvDatafeed
    # Comment out for offline testing
    try:
        all_regimes = model.run()
    except Exception as e:
        print(f"Error fetching data: {e}")
        print("Skipping live data test. Use with live data when ready.")
        return
    
    # --- Step 2: Prepare Returns Data ---
    print("\n[Step 3] Preparing returns data...")
    
    # Get individual asset returns from the fetched data
    assets = ["BTC", "ETH", "ALT", "STABLE"]
    returns_data = {}
    
    # Extract returns from features_dict
    for ticker, df in model.features_dict.items():
        if ticker == "BTC.D":
            returns_data["BTC"] = df["log_return"].apply(lambda x: np.exp(x) - 1)
        elif ticker == "ETH.D":
            returns_data["ETH"] = df["log_return"].apply(lambda x: np.exp(x) - 1)
        elif ticker == "TOTAL3ES":
            returns_data["ALT"] = df["log_return"].apply(lambda x: np.exp(x) - 1)
        elif ticker == "STABLE.C.D":
            # Stablecoins have ~0% volatility
            returns_data["STABLE"] = pd.Series(0.0, index=df.index)
    
    # Create returns DataFrame
    returns_df = pd.DataFrame(returns_data).dropna()
    
    # Align with regimes
    common_dates = returns_df.index.intersection(all_regimes.index)
    returns_aligned = returns_df.loc[common_dates].copy()
    regimes_aligned = all_regimes.loc[common_dates].copy()
    
    print(f"Aligned data: {len(returns_aligned)} samples")
    print(f"Date range: {returns_aligned.index[0]} to {returns_aligned.index[-1]}")
    
    # --- Step 3: Run Allocation Strategy ---
    print("\n[Step 4] Running allocation strategy...")
    
    strategy = AllocationStrategy(assets=assets)
    result = strategy.allocate(returns_aligned, regimes_aligned["season"])
    
    # --- Step 4: Performance Metrics ---
    print("\n[Step 5] Calculating performance metrics...")
    
    metrics = strategy.calculate_performance(result)
    
    print("\n" + "=" * 80)
    print("PERFORMANCE RESULTS")
    print("=" * 80)
    print(f"Total Return:        {metrics['total_return']:>10.2%}")
    print(f"Annualized Return:   {metrics['annual_return']:>10.2%}")
    print(f"Annualized Volatility: {metrics['volatility']:>8.2%}")
    print(f"Sharpe Ratio:        {metrics['sharpe_ratio']:>10.3f}")
    print(f"Max Drawdown:        {metrics['max_drawdown']:>10.2%}")
    print(f"Final Portfolio Value: {metrics['final_value']:>8.2f}")
    
    # --- Regime Distribution ---
    print("\n" + "=" * 80)
    print("REGIME DISTRIBUTION")
    print("=" * 80)
    regime_counts = result["Regime"].value_counts()
    for regime, count in regime_counts.items():
        pct = 100 * count / len(result)
        print(f"{regime:<30} {count:>6d} ({pct:>5.1f}%)")
    
    # --- Sample Allocations ---
    print("\n" + "=" * 80)
    print("SAMPLE ALLOCATIONS BY REGIME")
    print("=" * 80)
    from allocation import REGIME_CONSTRAINTS, get_bounds
    
    for regime, bounds_dict in REGIME_CONSTRAINTS.items():
        bounds = get_bounds(assets, bounds_dict)
        print(f"\n{regime}:")
        for asset, (low, high) in zip(assets, bounds):
            print(f"  {asset:<8} {low:>5.1%} - {high:>5.1%}")
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    
    return result, metrics


if __name__ == "__main__":
    result, metrics = main()
