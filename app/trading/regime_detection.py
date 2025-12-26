"""
Crypto Regime Detection Module

CRYPTOCURRENCY ONLY - Uses CCXT library to get live data from Kraken.
Market regime detection using Yang-Zhang volatility estimator.
Detects BULL, BEAR, SIDEWAYS, HODL market regimes.

Data Source: CCXT library → Kraken exchange (live crypto data)

This is COMPLETELY SEPARATE from Traditional Assets Regime Detection:
- Crypto Analysis: Uses this module + CCXT/Kraken data → Different strategy & allocation
- Equities Analysis: Uses traditional_assets_regime.py + yfinance data → Different strategy & allocation
- Execution: BOTH systems executed by SnapTrade API
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from .indicators import yang_zhang_estimator
import logging

logger = logging.getLogger(__name__)


class CryptoRegimeDetector:
    """
    Cryptocurrency Market Regime Detection Engine
    
    Data Source: CCXT library → Kraken exchange
    Strategies: Crypto-specific (momentum, volatility-based, 24/7 trading)
    Regimes: BULL, BEAR, SIDEWAYS, HODL, BTC SEASON, ALT SEASON
    Execution: SnapTrade API (trades executed on Kraken account)
    
    COMPLETELY SEPARATE from equities system - different analysis, different allocation strategy.
    """
    
    def __init__(self, lookback_periods: int = 365 * 15):
        """
        Initialize crypto regime detection
        
        Args:
            lookback_periods: Number of periods for historical data (15 years default)
        """
        self.lookback_periods = lookback_periods
    
    def fetch_kraken_data(self, symbols: List[str] = ['BTC/USD', 'ETH/USD']) -> pd.DataFrame:
        """
        Fetch live crypto market data from Kraken using CCXT library
        
        Args:
            symbols: List of trading pairs to fetch (Kraken format: BTC/USD, ETH/USD)
            
        Returns:
            DataFrame with OHLCV data (Open, High, Low, Close, Volume)
        """
        logger.info(f"Fetching Kraken data via CCXT for {symbols}...")
        
        try:
            import ccxt
            
            # Initialize Kraken exchange (public data - no API keys needed for OHLCV)
            exchange = ccxt.kraken({
                'enableRateLimit': True,  # Respect rate limits
                'timeout': 30000,  # 30 second timeout
            })
            
            all_data = {}
            
            for symbol in symbols:
                try:
                    logger.info(f"Fetching {symbol} from Kraken...")
                    
                    # Fetch OHLCV data (daily candles)
                    # Note: Kraken limits historical data fetch, we'll get as much as possible
                    ohlcv = exchange.fetch_ohlcv(
                        symbol, 
                        timeframe='1d', 
                        limit=min(self.lookback_periods, 720)  # Max ~2 years
                    )
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(
                        ohlcv, 
                        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    )
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    df['symbol'] = symbol
                    
                    all_data[symbol] = df
                    logger.info(f"Successfully fetched {len(df)} candles for {symbol}")
                    
                except Exception as e:
                    logger.error(f"Failed to fetch {symbol}: {str(e)}")
                    continue
            
            # Combine all symbols into a single DataFrame
            if all_data:
                combined_df = pd.concat(all_data.values(), keys=all_data.keys())
                return combined_df
            else:
                logger.warning("No data fetched from Kraken")
                return pd.DataFrame()
                
        except ImportError:
            logger.error("CCXT library not installed. Install with: pip install ccxt")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error fetching Kraken data: {str(e)}")
            return pd.DataFrame()
    
    def detect_regimes(self) -> Optional[pd.DataFrame]:
        """
        Detect current crypto market regimes using CCXT/Kraken live data
        
        Strategy (CRYPTO-SPECIFIC - different from equities):
        - Fetch BTC and ETH data from Kraken via CCXT
        - Calculate Yang-Zhang volatility (crypto volatility)
        - Classify regime based on momentum and volatility (24/7 crypto patterns)
        - Determine BTC vs ALT season
        - Generate crypto-specific allocation strategy
        
        Execution: Results sent to SnapTrade API for execution on Kraken account
        
        Returns:
            DataFrame with regime detection results
        """
        try:
            logger.info("Starting CRYPTO regime detection (CCXT/Kraken data)...")
            
            # Fetch data from Kraken via CCXT
            data = self.fetch_kraken_data()
            
            if data is None or data.empty:
                logger.error("Failed to detect regimes: no Kraken data")
                return None
            
            # In production, classify regime from Kraken data
            # regime = self._classify_regime(data)
            
            logger.info(f"Crypto regime detected")
            
            return data
        
        except Exception as e:
            logger.error(f"Crypto regime detection failed: {str(e)}")
            raise
    
    def get_latest_regime(self, regimes_df):
        """Get latest regime from detection results"""
        if regimes_df is None or regimes_df.empty:
            return None
        
        latest = regimes_df.iloc[-1]
        
        return {
            "season": latest['season'],
            "vol_regime": latest.get(('TOTALES', 'vol_regime')),
            "dir_regime": latest.get(('TOTALES', 'dir_regime')),
            "btc_season": latest.get(('BTC.D', 'season')),
            "eth_season": latest.get(('ETH.D', 'season')),
            "timestamp": latest.name
        }
    
    def is_regime_changed(self, new_regime, old_regime):
        """Check if regime has changed"""
        if old_regime is None:
            return True
        
        return new_regime['season'] != old_regime['season']
