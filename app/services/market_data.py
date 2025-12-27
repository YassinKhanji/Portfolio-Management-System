"""
Market Data Service

Fetch and cache market data from CCXT/Kraken and yfinance.
"""

try:
    from app.trading.regime_detection import CryptoRegimeDetector
except Exception:
    CryptoRegimeDetector = None  # type: ignore
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class MarketDataService:
    """Manage market data fetching and caching"""
    
    def __init__(self):
        self.crypto_detector = None
        if CryptoRegimeDetector is not None:
            try:
                self.crypto_detector = CryptoRegimeDetector(lookback_periods=365 * 15)
            except Exception as e:
                logger.warning(f"Failed to initialize CryptoRegimeDetector: {e}")
        self.last_refresh = None
        self.cached_data = None
    
    def refresh_market_data(self, force: bool = False):
        """
        Fetch latest market data
        
        Args:
            force: Force refresh even if recently cached
        
        Returns:
            DataFrame with market data
        """
        try:
            logger.info("Refreshing market data...")
            
            # Check cache
            if not force and self.last_refresh:
                age_minutes = (datetime.utcnow() - self.last_refresh).total_seconds() / 60
                if age_minutes < 240:  # Less than 4 hours
                    logger.info(f"Using cached data (age: {age_minutes:.0f}m)")
                    return self.cached_data
            
            # Fetch new data via detector/model if available
            if self.crypto_detector is not None and hasattr(self.crypto_detector, "run"):
                self.cached_data = self.crypto_detector.run()
            else:
                logger.info("No crypto regime detector available; skipping data fetch")
                self.cached_data = self.cached_data or None
            self.last_refresh = datetime.utcnow()
            
            logger.info("Market data refreshed successfully")
            return self.cached_data
        
        except Exception as e:
            logger.error(f"Market data refresh failed: {str(e)}")
            # Return cached data if available
            if self.cached_data is not None:
                logger.warning("Using stale cached data")
                return self.cached_data
            raise
    
    def get_latest_prices(self):
        """Get latest prices from cached data"""
        if self.cached_data is None:
            return None
        
        latest = self.cached_data.iloc[-1]
        
        return {
            "btc": latest.get(('BTC.D', 'close')),
            "eth": latest.get(('ETH.D', 'close')),
            "timestamp": latest.name
        }
    
    def get_data_age(self) -> Optional[int]:
        """Get age of cached data in minutes"""
        if self.last_refresh is None:
            return None
        
        age_minutes = (datetime.utcnow() - self.last_refresh).total_seconds() / 60
        return int(age_minutes)
