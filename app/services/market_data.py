"""
Market Data Service

Fetch and cache market data from CCXT/Kraken and yfinance.
Provides real-time price quotes for crypto and equity assets.
"""

try:
    from app.trading.regime_detection import CryptoRegimeDetector
except Exception:
    CryptoRegimeDetector = None  # type: ignore
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from functools import lru_cache
import time

logger = logging.getLogger(__name__)

# Symbol mapping for crypto assets (SnapTrade symbols -> Kraken trading pairs)
# Using CAD pairs where available, USD pairs as fallback
CRYPTO_SYMBOL_MAP = {
    # CAD pairs available on Kraken
    "XETH": "ETH/CAD",
    "ETH": "ETH/CAD",
    "XXBT": "BTC/CAD",
    "BTC": "BTC/CAD",
    "XXDG": "DOGE/CAD",
    "XDG": "DOGE/CAD",
    "DOGE": "DOGE/CAD",
    "SOL": "SOL/CAD",
    "SOL03": "SOL/CAD",  # Staked SOL variant
    "XRP": "XRP/CAD",
    # USD pairs only (no CAD available) - will need conversion
    "KSM": "KSM/USD",
    "KSM07": "KSM/USD",  # Staked KSM variant
    "ATOM": "ATOM/USD",
    "ATOM21": "ATOM/USD",  # Staked ATOM variant
    "DOT": "DOT/USD",
    "DOT28": "DOT/USD",  # Staked DOT variant
    "MATIC": "MATIC/USD",
    "ADA": "ADA/USD",
    "LINK": "LINK/USD",
    "AVAX": "AVAX/USD",
    "LTC": "LTC/USD",
    "XLM": "XLM/USD",
    "TRX": "TRX/USD",
    "EGLD": "EGLD/USD",
}

# Cache for live prices (symbol -> (price_usd, timestamp))
_price_cache: Dict[str, tuple] = {}
_PRICE_CACHE_TTL_SECONDS = 60  # Cache prices for 60 seconds


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
                age_minutes = (datetime.now(timezone.utc) - self.last_refresh).total_seconds() / 60
                if age_minutes < 240:  # Less than 4 hours
                    logger.info(f"Using cached data (age: {age_minutes:.0f}m)")
                    return self.cached_data
            
            # Fetch new data via detector/model if available
            if self.crypto_detector is not None and hasattr(self.crypto_detector, "run"):
                self.cached_data = self.crypto_detector.run()
            else:
                logger.info("No crypto regime detector available; skipping data fetch")
                self.cached_data = self.cached_data or None
            self.last_refresh = datetime.now(timezone.utc)
            
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
        
        age_minutes = (datetime.now(timezone.utc) - self.last_refresh).total_seconds() / 60
        return int(age_minutes)


def get_live_crypto_prices(symbols: List[str]) -> Dict[str, float]:
    """
    Fetch live cryptocurrency prices from Kraken via CCXT.
    Prices are returned in CAD (using CAD pairs where available, converting USD pairs).
    
    Args:
        symbols: List of crypto symbols (e.g., ["XETH", "XXDG", "KSM07"])
        
    Returns:
        Dict mapping symbols to their current CAD prices
    """
    global _price_cache
    
    from app.core.currency import convert_to_cad
    
    logger.info(f"=== get_live_crypto_prices CALLED with symbols: {symbols} ===")
    
    prices = {}
    symbols_to_fetch = []
    current_time = time.time()
    
    # Check cache first
    for symbol in symbols:
        symbol_upper = symbol.upper()
        if symbol_upper in _price_cache:
            cached_price, cached_time = _price_cache[symbol_upper]
            if current_time - cached_time < _PRICE_CACHE_TTL_SECONDS:
                prices[symbol_upper] = cached_price
                logger.debug(f"Using cached price for {symbol_upper}: ${cached_price}")
                continue
        
        # Map to Kraken trading pair
        kraken_pair = CRYPTO_SYMBOL_MAP.get(symbol_upper)
        if kraken_pair:
            symbols_to_fetch.append((symbol_upper, kraken_pair))
        else:
            # Try to construct pair from symbol
            base_symbol = ''.join(c for c in symbol_upper if not c.isdigit())
            if base_symbol != symbol_upper and base_symbol in CRYPTO_SYMBOL_MAP:
                kraken_pair = CRYPTO_SYMBOL_MAP[base_symbol]
                symbols_to_fetch.append((symbol_upper, kraken_pair))
                logger.info(f"Mapped staking variant {symbol_upper} to base {base_symbol}")
    
    if not symbols_to_fetch:
        return prices
    
    try:
        import ccxt
        
        # Initialize Kraken exchange (public data - no API keys needed)
        exchange = ccxt.kraken({
            'enableRateLimit': True,
            'timeout': 10000,  # 10 second timeout for price quotes
        })
        
        # Fetch tickers for all symbols at once
        unique_pairs = list(set(pair for _, pair in symbols_to_fetch))
        
        try:
            # Try to fetch all tickers at once
            tickers = exchange.fetch_tickers(unique_pairs)
            
            for original_symbol, kraken_pair in symbols_to_fetch:
                if kraken_pair in tickers:
                    ticker = tickers[kraken_pair]
                    price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or 0
                    if price > 0:
                        # Check if this is a USD pair - convert to CAD if so
                        if kraken_pair.endswith('/USD'):
                            price_cad = convert_to_cad(float(price), "USD")
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} USD = ${price_cad} CAD")
                            price = price_cad
                        else:
                            # Already in CAD
                            price = float(price)
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} CAD")
                        
                        prices[original_symbol] = price
                        _price_cache[original_symbol] = (price, current_time)
        except Exception as e:
            logger.warning(f"Batch ticker fetch failed, trying individual: {e}")
            # Fallback: fetch individually
            for original_symbol, kraken_pair in symbols_to_fetch:
                try:
                    ticker = exchange.fetch_ticker(kraken_pair)
                    price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or 0
                    if price > 0:
                        # Check if this is a USD pair - convert to CAD if so
                        if kraken_pair.endswith('/USD'):
                            price_cad = convert_to_cad(float(price), "USD")
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} USD = ${price_cad} CAD")
                            price = price_cad
                        else:
                            price = float(price)
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} CAD")
                        
                        prices[original_symbol] = price
                        _price_cache[original_symbol] = (price, current_time)
                except Exception as inner_e:
                    logger.warning(f"Failed to fetch price for {original_symbol} ({kraken_pair}): {inner_e}")
                    
    except ImportError:
        logger.error("CCXT library not installed. Cannot fetch live crypto prices.")
    except Exception as e:
        logger.error(f"Error fetching live crypto prices: {e}")
    
    return prices


def get_live_equity_price(symbol: str) -> Optional[float]:
    """
    Fetch live equity/stock price using yfinance.
    
    Args:
        symbol: Stock ticker symbol (e.g., "SHOP.TO", "AAPL")
        
    Returns:
        Current price in the stock's native currency, or None if unavailable
    """
    global _price_cache
    
    symbol_upper = symbol.upper()
    current_time = time.time()
    
    # Check cache
    if symbol_upper in _price_cache:
        cached_price, cached_time = _price_cache[symbol_upper]
        if current_time - cached_time < _PRICE_CACHE_TTL_SECONDS:
            logger.debug(f"Using cached price for {symbol_upper}: ${cached_price}")
            return cached_price
    
    try:
        import yfinance as yf
        
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        # Try different price fields
        price = (
            info.get('regularMarketPrice') or
            info.get('currentPrice') or
            info.get('previousClose') or
            info.get('ask') or
            info.get('bid')
        )
        
        if price and price > 0:
            _price_cache[symbol_upper] = (float(price), current_time)
            logger.info(f"Fetched live price for {symbol_upper}: ${price}")
            return float(price)
            
    except ImportError:
        logger.error("yfinance library not installed. Cannot fetch live equity prices.")
    except Exception as e:
        logger.warning(f"Failed to fetch price for {symbol}: {e}")
    
    return None


def update_holdings_with_live_prices(
    holdings: List[Dict],
    broker: str = "kraken"
) -> List[Dict]:
    """
    Update a list of holdings with live market prices.
    
    For crypto holdings (Kraken), fetches live prices from CCXT.
    For equity holdings, fetches prices from yfinance.
    
    Args:
        holdings: List of holding dicts with 'symbol' and 'price' keys
        broker: Broker name to determine asset type
        
    Returns:
        Updated holdings list with live prices
    """
    if not holdings:
        return holdings
    
    is_crypto = broker.lower() in ["kraken"]
    
    if is_crypto:
        # Get all crypto symbols
        symbols = [h.get('symbol', '') for h in holdings if h.get('symbol')]
        live_prices = get_live_crypto_prices(symbols)
        
        for holding in holdings:
            symbol = holding.get('symbol', '').upper()
            if symbol in live_prices:
                old_price = holding.get('price', 0)
                new_price = live_prices[symbol]
                holding['price'] = new_price
                
                # Recalculate market value
                quantity = holding.get('quantity', 0)
                if quantity > 0:
                    holding['market_value'] = quantity * new_price
                    
                logger.info(f"Updated {symbol} price: {old_price} -> {new_price}")
    else:
        # Equity - fetch prices individually (less common)
        for holding in holdings:
            symbol = holding.get('symbol', '')
            # Skip cash positions
            if symbol.upper() in ['USD', 'CAD', 'EUR', 'GBP', 'JPY', 'USDC', 'USDT']:
                continue
                
            live_price = get_live_equity_price(symbol)
            if live_price:
                old_price = holding.get('price', 0)
                holding['price'] = live_price
                
                # Recalculate market value
                quantity = holding.get('quantity', 0)
                if quantity > 0:
                    holding['market_value'] = quantity * live_price
                    
                logger.info(f"Updated {symbol} price: {old_price} -> {live_price}")
    
    return holdings
