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

# Symbol mapping for crypto assets (SnapTrade symbols -> base asset symbol).
# Quote currency selection is dynamic (prefer CAD markets on Kraken).
CRYPTO_BASE_MAP: Dict[str, str] = {
    # Kraken legacy asset codes
    "XETH": "ETH",
    "XXBT": "BTC",
    "XXDG": "DOGE",
    "XDG": "DOGE",
    "ZUSD": "USD",
    "ZCAD": "CAD",

    # Common spot symbols
    "ETH": "ETH",
    "BTC": "BTC",
    "DOGE": "DOGE",
    "SOL": "SOL",
    "XRP": "XRP",
    "KSM": "KSM",
    "ATOM": "ATOM",
    "DOT": "DOT",
    "MATIC": "MATIC",
    "ADA": "ADA",
    "LINK": "LINK",
    "AVAX": "AVAX",
    "LTC": "LTC",
    "XLM": "XLM",
    "TRX": "TRX",
    "EGLD": "EGLD",

    # Staking / derivative variants (prefer base token price)
    "SOL03": "SOL",
    "KSM07": "KSM",
    "ATOM21": "ATOM",
    "DOT28": "DOT",
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
    
    logger.info(f"=== get_live_crypto_prices CALLED with symbols: {symbols} ===")
    
    prices: Dict[str, float] = {}
    symbols_to_fetch: List[tuple[str, str]] = []
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
        
        # Normalize to a base symbol (handles staking variants like KSM07)
        base_symbol = CRYPTO_BASE_MAP.get(symbol_upper)
        if not base_symbol:
            stripped = ''.join(c for c in symbol_upper if not c.isdigit())
            base_symbol = CRYPTO_BASE_MAP.get(stripped) or stripped
            if stripped != symbol_upper and base_symbol:
                logger.info(f"Mapped staking variant {symbol_upper} to base {base_symbol}")

        # Select best quote currency dynamically after markets are loaded
        symbols_to_fetch.append((symbol_upper, base_symbol))
    
    if not symbols_to_fetch:
        return prices
    
    try:
        import ccxt
        
        # Initialize Kraken exchange (public data - no API keys needed)
        exchange = ccxt.kraken({
            'enableRateLimit': True,
            'timeout': 10000,  # 10 second timeout for price quotes
        })
        
        exchange.load_markets()

        # Use Kraken's own USD/CAD rate for conversions to match Kraken app pricing.
        # Cache it with the same TTL as other prices.
        fx_key = "FX_USD_CAD"
        usd_cad_rate: Optional[float] = None
        if fx_key in _price_cache:
            cached_rate, cached_time = _price_cache[fx_key]
            if current_time - cached_time < _PRICE_CACHE_TTL_SECONDS:
                usd_cad_rate = float(cached_rate)

        if usd_cad_rate is None and 'USD/CAD' in exchange.markets:
            try:
                fx_ticker = exchange.fetch_ticker('USD/CAD')
                fx_price = fx_ticker.get('last') or fx_ticker.get('close') or fx_ticker.get('bid') or 0
                if fx_price and float(fx_price) > 0:
                    usd_cad_rate = float(fx_price)
                    _price_cache[fx_key] = (usd_cad_rate, current_time)
                    logger.info(f"Fetched USD/CAD rate from Kraken: {usd_cad_rate}")
            except Exception as fx_exc:  # noqa: BLE001
                logger.warning(f"Failed to fetch USD/CAD rate from Kraken: {fx_exc}")

        def pick_pair(base: str) -> Optional[str]:
            # Prefer CAD pricing; fallback to USD/USDT. Only choose pairs Kraken actually supports.
            candidates = [f"{base}/CAD", f"{base}/USD", f"{base}/USDT"]
            for candidate in candidates:
                if candidate in exchange.markets:
                    return candidate
            return None

        resolved: List[tuple[str, str]] = []
        for original_symbol, base_symbol in symbols_to_fetch:
            chosen = pick_pair(base_symbol)
            if chosen:
                resolved.append((original_symbol, chosen))
            else:
                logger.warning(f"No supported Kraken market found for {original_symbol} (base={base_symbol})")

        if not resolved:
            return prices

        # Fetch tickers for all symbols at once
        unique_pairs = list(set(pair for _, pair in resolved))
        
        try:
            # Try to fetch all tickers at once
            tickers = exchange.fetch_tickers(unique_pairs)
            
            for original_symbol, kraken_pair in resolved:
                if kraken_pair in tickers:
                    ticker = tickers[kraken_pair]
                    price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or 0
                    if price > 0:
                        quote = (kraken_pair.split('/')[-1] or '').upper()
                        if quote == 'CAD':
                            price = float(price)
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} CAD")
                        else:
                            # Treat USD/USDT as USD for FX conversion. Prefer Kraken's USD/CAD rate.
                            if usd_cad_rate is not None:
                                price_cad = float(price) * float(usd_cad_rate)
                                logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} {quote} * {usd_cad_rate} = ${price_cad} CAD")
                                price = price_cad
                            else:
                                from app.core.currency import convert_to_cad
                                price_cad = convert_to_cad(float(price), "USD")
                                logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} {quote} -> ${price_cad} CAD (fallback FX)")
                                price = price_cad
                        
                        prices[original_symbol] = price
                        _price_cache[original_symbol] = (price, current_time)
        except Exception as e:
            logger.warning(f"Batch ticker fetch failed, trying individual: {e}")
            # Fallback: fetch individually
            for original_symbol, kraken_pair in resolved:
                try:
                    ticker = exchange.fetch_ticker(kraken_pair)
                    price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or 0
                    if price > 0:
                        quote = (kraken_pair.split('/')[-1] or '').upper()
                        if quote == 'CAD':
                            price = float(price)
                            logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} CAD")
                        else:
                            if usd_cad_rate is not None:
                                price_cad = float(price) * float(usd_cad_rate)
                                logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} {quote} * {usd_cad_rate} = ${price_cad} CAD")
                                price = price_cad
                            else:
                                from app.core.currency import convert_to_cad
                                price_cad = convert_to_cad(float(price), "USD")
                                logger.info(f"Fetched {original_symbol} ({kraken_pair}): ${price} {quote} -> ${price_cad} CAD (fallback FX)")
                                price = price_cad
                        
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
