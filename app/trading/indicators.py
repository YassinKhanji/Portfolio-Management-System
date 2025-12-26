import numpy as np
import pandas as pd

def yang_zhang_estimator(high, low, open, close, window=30, trading_periods=365):
    """
    Calculate Yang-Zhang Volatility Estimator.
    
    Args:
        high (np.array): High prices
        low (np.array): Low prices
        open (np.array): Open prices
        close (np.array): Close prices
        window (int): Rolling window size
        trading_periods (int): Number of trading periods in a year (default 365 for crypto)
        
    Returns:
        pd.Series: Yang-Zhang volatility
    """
    
    # Ensure inputs are pandas Series for rolling operations, but handle numpy arrays too
    if isinstance(open, np.ndarray):
        open = pd.Series(open)
    if isinstance(high, np.ndarray):
        high = pd.Series(high)
    if isinstance(low, np.ndarray):
        low = pd.Series(low)
    if isinstance(close, np.ndarray):
        close = pd.Series(close)
    
    # Constants
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    
    # Log prices
    log_open = np.log(open)
    log_high = np.log(high)
    log_low = np.log(low)
    log_close = np.log(close)
    
    # Overnight volatility (close to open) - For crypto this is less relevant but part of the formula
    # Represented by Close(-1) to Open(0) gap
    log_close_shifted = log_close.shift(1)
    
    # Rogers-Satchell Volatility
    rs_vol = (log_high - log_close) * (log_high - log_open) + \
             (log_low - log_close) * (log_low - log_open)
    
    # Open-Close Volatility
    oc_vol = (np.log(open) - np.log(close.shift(1)))**2
    
    # Open-Open Volatility (Overnight)
    # Variance of (Open - Close(-1))
    # We calculate rolling variance of the overnight returns
    overnight_rets = log_open - log_close_shifted
    var_open = overnight_rets.rolling(window=window).var()
    
    # Window-Open Volatility
    # Variance of (Close - Open)
    open_close_rets = log_close - log_open
    var_close = open_close_rets.rolling(window=window).var()
    
    # Rolling Rogers-Satchell
    rs_vol_rolling = rs_vol.rolling(window=window).mean()
    
    # Yang-Zhang Variance
    yz_var = var_open + k * var_close + (1 - k) * rs_vol_rolling
    
    # Annualized Volatility
    yz_vol = np.sqrt(yz_var) * np.sqrt(trading_periods)
    
    return yz_vol
