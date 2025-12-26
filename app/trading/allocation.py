"""
Portfolio Allocation Engine

Implements portfolio optimization and rebalancing strategies based on market regime.
Supports Distributionally Robust Optimization (DRO) with regime-specific constraints.
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize


# --- Portfolio Performance Metrics ---

def calculate_stats(returns, weights):
    """
    Calculate key portfolio performance metrics.
    
    Args:
        returns (np.array): 2D array of asset returns (samples x assets)
        weights (np.array): Portfolio weights (sum to 1)
        
    Returns:
        tuple: (total_return, sharpe, sortino, calmar, starr, port_returns)
    """
    port_returns = returns @ weights
    mean = port_returns.mean()
    std = port_returns.std()
    downside_std = port_returns[port_returns < 0].std()
    max_drawdown = (port_returns.cumsum().cummax() - port_returns.cumsum()).max()
    var_95 = np.percentile(port_returns, 5)
    cvar_95 = port_returns[port_returns <= var_95].mean() if len(port_returns[port_returns <= var_95]) > 0 else 0

    sharpe = mean / std if std else 0
    sortino = mean / downside_std if downside_std else 0
    calmar = mean / max_drawdown if max_drawdown else 0
    starr = mean / abs(cvar_95) if cvar_95 else 0
    total_return = port_returns.sum()

    return total_return, sharpe, sortino, calmar, starr, port_returns


# --- Optimization Functions ---

def optimize_weights(returns, objective="sharpe", bounds=None):
    """
    Optimize portfolio weights using scipy.minimize.
    
    Args:
        returns (np.array): 2D array of asset returns
        objective (str): Optimization target ("return", "sharpe", "sortino", "calmar", "starr")
        bounds (list): Bounds for each asset [(low, high), ...]
        
    Returns:
        np.array: Optimized weights or None if optimization fails
    """
    n_assets = returns.shape[1]
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1}
    
    if bounds is None:
        bounds = [(0, 1)] * n_assets

    def loss(w):
        total, sharpe, sortino, calmar, starr, _ = calculate_stats(returns, w)
        if objective == "return":
            return -total
        elif objective == "sharpe":
            return -sharpe
        elif objective == "sortino":
            return -sortino
        elif objective == "calmar":
            return -calmar
        elif objective == "starr":
            return -starr
        else:
            raise ValueError(f"Invalid objective: {objective}")

    init_w = np.ones(n_assets) / n_assets
    result = minimize(loss, init_w, bounds=bounds, constraints=constraints, method='SLSQP')

    if result.success and not np.isnan(result.x).any():
        return result.x
    else:
        return None


def get_bounds(assets, regime_bounds):
    """
    Convert regime constraints dict to bounds tuple for each asset.
    
    Args:
        assets (list): Asset names (order matters)
        regime_bounds (dict): Regime constraints {asset: (low, high), "others": (low, high)}
        
    Returns:
        list: Bounds [(low, high), ...] for each asset
    """
    bounds = []
    for asset in assets:
        if asset in regime_bounds:
            bounds.append(regime_bounds[asset])
        else:
            bounds.append(regime_bounds.get("others", (0, 1)))
    return bounds


def fallback_within_bounds(bounds):
    """
    Create weights that satisfy bounds (safe fallback when optimization fails).
    
    Args:
        bounds (list): Bounds [(low, high), ...] for each asset
        
    Returns:
        np.array: Normalized weights within bounds
    """
    weights = np.array([(low + high) / 2 for (low, high) in bounds])
    total = weights.sum()
    
    if total == 0:
        weights = np.array([1.0 / len(bounds)] * len(bounds))
    else:
        weights /= total

    # Clip and renormalize
    lower_bounds = np.array([low for low, _ in bounds])
    upper_bounds = np.array([high for _, high in bounds])
    weights = np.clip(weights, lower_bounds, upper_bounds)
    weights /= weights.sum()
    
    return weights


# --- Regime Configuration ---

REGIME_OBJECTIVES = {
    "Risk Off": "starr",                              # Minimize tail risk
    "BTC Season": "sharpe",                           # Risk-adjusted returns
    "Altcoin Season + ETH Season": "sortino",         # Downside risk focus
    "Risk On": "return",                              # Growth
    "HODL": "calmar"                                  # Return / Max Drawdown
}

REGIME_CONSTRAINTS = {
    "Risk Off": {
        "BTC": (0, 0.2),
        "ETH": (0, 0.2),
        "ALT": (0, 0.1),
        "STABLE": (0.6, 1.0),
        "others": (0, 0)
    },
    "BTC Season": {
        "BTC": (0.5, 1.0),
        "ETH": (0.1, 0.4),
        "ALT": (0, 0.1),
        "STABLE": (0, 0.3),
        "others": (0, 0)
    },
    "Altcoin Season + ETH Season": {
        "BTC": (0.1, 0.4),
        "ETH": (0.3, 0.6),
        "ALT": (0.2, 0.5),
        "STABLE": (0, 0.2),
        "others": (0, 0)
    },
    "Risk On": {
        "BTC": (0.2, 0.4),
        "ETH": (0.2, 0.4),
        "ALT": (0.2, 0.4),
        "STABLE": (0, 0.2),
        "others": (0, 0)
    },
    "HODL": {
        "BTC": (0.25, 0.25),
        "ETH": (0.25, 0.25),
        "ALT": (0.25, 0.25),
        "STABLE": (0.25, 0.25),
        "others": (0.25, 0.25)
    }
}


# --- Strategy Engine ---

class AllocationStrategy:
    """
    Portfolio allocation engine using regime-based constraints and dynamic optimization.
    """
    
    def __init__(self, assets=None, rebalance_frequency=42, window_size=14, num_buckets=3):
        """
        Initialize the allocation strategy.
        
        Args:
            assets (list): Asset names ["BTC", "ETH", "ALT", "STABLE"]
            rebalance_frequency (int): Bars between rebalancing (42 = 1 week on 4h)
            window_size (int): Historical bars for optimization (14 = 14 bars)
            num_buckets (int): Number of staggered portfolios (3 for 3-way split)
        """
        self.assets = assets or ["BTC", "ETH", "ALT", "STABLE"]
        self.rebalance_frequency = rebalance_frequency
        self.window_size = window_size
        self.num_buckets = num_buckets
    
    def allocate(self, returns_df, regimes_series=None):
        """
        Run staggered allocation strategy with regime-based constraints.
        
        Args:
            returns_df (DataFrame): Asset returns with datetime index
            regimes_series (Series): Market regimes (optional, defaults to "HODL")
            
        Returns:
            DataFrame: Input data plus "optimized" and "cumulative_optimized" columns
        """
        result = returns_df.copy()
        
        # Default to HODL if no regimes provided
        if regimes_series is None:
            result["Regime"] = "HODL"
        else:
            result["Regime"] = regimes_series.values
        
        # Staggered bucket returns
        bucket_returns = [np.zeros(len(result)) for _ in range(self.num_buckets)]
        returns_array = result[self.assets].values
        regimes = result["Regime"].values
        
        last_valid_regime = "Risk Off"  # Fallback regime
        
        # Process each bucket with staggered starts
        for bucket in range(self.num_buckets):
            weights = None
            
            for i in range(bucket, len(result)):
                # Rebalance at specified frequency
                if (i - bucket) % self.rebalance_frequency == 0:
                    if i - self.window_size >= 0:
                        regime = regimes[i]
                        
                        # Validate regime, fallback if unknown
                        if regime not in REGIME_OBJECTIVES or regime not in REGIME_CONSTRAINTS:
                            regime = last_valid_regime
                        else:
                            last_valid_regime = regime
                        
                        # Extract window for optimization
                        window_returns = result[self.assets].iloc[i - self.window_size:i]
                        
                        # Get objective and constraints for regime
                        objective = REGIME_OBJECTIVES[regime]
                        bounds = get_bounds(self.assets, REGIME_CONSTRAINTS[regime])
                        
                        # Optimize weights
                        weights = optimize_weights(
                            window_returns.values,
                            objective=objective,
                            bounds=bounds
                        )
                        
                        # Fallback if optimization fails
                        if weights is None or np.isnan(weights).any():
                            weights = fallback_within_bounds(bounds)
                    else:
                        # Not enough data, use equal weight
                        weights = np.ones(len(self.assets)) / len(self.assets)
                
                # Apply weights to current period
                if weights is not None:
                    daily_ret = np.dot(returns_array[i], weights)
                    bucket_returns[bucket][i] = daily_ret
        
        # Average bucket returns to smooth transitions
        result["optimized"] = np.mean(bucket_returns, axis=0)
        result["cumulative_optimized"] = (1 + result["optimized"]).cumprod()
        
        # Drop warmup period
        valid_start = max(range(self.num_buckets)) + self.rebalance_frequency
        result = result.iloc[valid_start:].copy()
        
        return result
    
    def calculate_performance(self, result_df):
        """
        Calculate performance metrics.
        
        Args:
            result_df (DataFrame): Result DataFrame with "optimized" column
            
        Returns:
            dict: Performance metrics
        """
        returns = result_df['optimized']
        cum_returns = result_df['cumulative_optimized']
        
        total_return = cum_returns.iloc[-1] - 1
        annual_return = (cum_returns.iloc[-1] ** (365.25 / len(result_df))) - 1
        volatility = returns.std() * np.sqrt(365.25 * 6)  # Annualized (4h bars)
        sharpe_ratio = annual_return / volatility if volatility > 0 else 0
        max_drawdown = (cum_returns / cum_returns.cummax() - 1).min()
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'volatility': volatility,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'final_value': cum_returns.iloc[-1]
        }


def run_dro_strategy(returns_df, regimes_df, assets=None):
    """
    Convenience function to run the full allocation strategy.
    
    Args:
        returns_df (DataFrame): Asset returns with datetime index
        regimes_df (DataFrame or Series): Market regimes (DataFrame with "season" column or Series)
        assets (list): Asset names to allocate
        
    Returns:
        DataFrame: Results with allocations and cumulative returns
    """
    if isinstance(regimes_df, pd.DataFrame):
        regimes = regimes_df["season"]
    else:
        regimes = regimes_df
    
    # Align indices
    common_dates = returns_df.index.intersection(regimes.index)
    returns_aligned = returns_df.loc[common_dates].copy()
    regimes_aligned = regimes.loc[common_dates].copy()
    
    strategy = AllocationStrategy(assets=assets)
    result = strategy.allocate(returns_aligned, regimes_aligned)
    
    return result
