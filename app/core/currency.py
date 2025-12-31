"""
Currency Conversion Utilities

Provides currency conversion functions for normalizing values to CAD.
Uses exchange rates from external APIs or cached values.
"""

import logging
from typing import Optional
from datetime import datetime, timedelta
import os

logger = logging.getLogger(__name__)

# Cache for exchange rates
_exchange_rate_cache: dict = {}
_cache_expiry: Optional[datetime] = None
_CACHE_DURATION = timedelta(hours=4)  # Cache for 4 hours

# Flag to track if we've already tried fetching rates this session
_initial_fetch_attempted = False

# Default exchange rates (fallback if API fails)
# Updated December 2024 - these are used when API is unavailable
DEFAULT_EXCHANGE_RATES = {
    "USD": 1.44,  # USD to CAD
    "EUR": 1.50,  # EUR to CAD
    "GBP": 1.82,  # GBP to CAD
    "JPY": 0.0094,  # JPY to CAD
    "CHF": 1.62,  # CHF to CAD
    "AUD": 0.91,  # AUD to CAD
    "CAD": 1.0,  # CAD to CAD (no conversion)
}


def _suppress_noisy_logging():
    """Suppress noisy third-party logging."""
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('peewee').setLevel(logging.WARNING)


def get_usd_to_cad_rate() -> float:
    """
    Get the current USD to CAD exchange rate.
    
    Returns:
        float: USD to CAD exchange rate
    """
    return _get_exchange_rate("USD")


def _get_exchange_rate(currency: str) -> float:
    """
    Get exchange rate for converting a currency to CAD.
    Uses cached values to minimize API calls.
    
    Args:
        currency: Source currency code (e.g., 'USD', 'EUR')
    
    Returns:
        float: Exchange rate to CAD
    """
    global _exchange_rate_cache, _cache_expiry, _initial_fetch_attempted
    
    currency = currency.upper()
    
    # CAD to CAD is always 1.0
    if currency == "CAD":
        return 1.0
    
    # Check cache first
    now = datetime.now()
    if _cache_expiry and now < _cache_expiry and currency in _exchange_rate_cache:
        return _exchange_rate_cache[currency]
    
    # If we haven't tried fetching yet this session, try once
    if not _initial_fetch_attempted:
        _initial_fetch_attempted = True
        _suppress_noisy_logging()
        
        try:
            rate = _fetch_exchange_rate_api(currency)
            if rate:
                _exchange_rate_cache[currency] = rate
                _cache_expiry = now + _CACHE_DURATION
                logger.info(f"Fetched live exchange rate: {currency}/CAD = {rate}")
                return rate
        except Exception as e:
            logger.debug(f"Exchange rate fetch failed, using defaults: {e}")
    
    # Use default rates
    if currency in DEFAULT_EXCHANGE_RATES:
        return DEFAULT_EXCHANGE_RATES[currency]
    
    # Unknown currency, assume 1:1
    logger.warning(f"Unknown currency {currency}, assuming 1:1 with CAD")
    return 1.0


def _fetch_exchange_rate_api(currency: str) -> Optional[float]:
    """
    Fetch exchange rate using a free API that doesn't spam logs.
    
    Args:
        currency: Source currency code
    
    Returns:
        Optional[float]: Exchange rate or None if fetch failed
    """
    # Try the Exchange Rate API (free, no key required)
    try:
        import requests
        
        url = f"https://api.exchangerate-api.com/v4/latest/{currency}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if 'rates' in data and 'CAD' in data['rates']:
                rate = float(data['rates']['CAD'])
                return rate
    except Exception:
        pass
    
    # Try Open Exchange Rates API (another free option)
    try:
        import requests
        
        url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if 'rates' in data:
                rates = data['rates']
                if currency == "USD" and 'CAD' in rates:
                    return float(rates['CAD'])
                elif currency in rates and 'CAD' in rates:
                    # Convert through USD: currency -> USD -> CAD
                    usd_rate = 1.0 / rates[currency]  # Currency to USD
                    cad_rate = rates['CAD']  # USD to CAD
                    return float(usd_rate * cad_rate)
    except Exception:
        pass
    
    return None


def convert_to_cad(amount: float, from_currency: str = "USD") -> float:
    """
    Convert an amount from a given currency to CAD.
    
    Args:
        amount: Amount in source currency
        from_currency: Source currency code (default: USD)
    
    Returns:
        float: Amount converted to CAD (full precision, no rounding)
    """
    if amount is None or amount == 0:
        return 0.0
    
    from_currency = (from_currency or "CAD").upper()
    
    # No conversion needed for CAD
    if from_currency == "CAD":
        return float(amount)
    
    rate = _get_exchange_rate(from_currency)
    converted = float(amount) * rate
    
    # Return full precision - rounding should only happen at display time
    return converted


def convert_from_cad(amount: float, to_currency: str = "USD") -> float:
    """
    Convert an amount from CAD to a given currency.
    
    Args:
        amount: Amount in CAD
        to_currency: Target currency code (default: USD)
    
    Returns:
        float: Amount converted to target currency
    """
    if amount is None or amount == 0:
        return 0.0
    
    to_currency = (to_currency or "CAD").upper()
    
    # No conversion needed for CAD
    if to_currency == "CAD":
        return float(amount)
    
    rate = _get_exchange_rate(to_currency)
    if rate == 0:
        return float(amount)
    
    converted = float(amount) / rate
    
    return round(converted, 2)


def refresh_exchange_rates() -> dict:
    """
    Force refresh of exchange rate cache.
    
    Returns:
        dict: Current exchange rates (currency -> CAD rate)
    """
    global _exchange_rate_cache, _cache_expiry, _initial_fetch_attempted
    
    # Reset fetch flag to allow retry
    _initial_fetch_attempted = False
    _cache_expiry = None
    _exchange_rate_cache.clear()
    
    # Trigger a fresh fetch
    _get_exchange_rate("USD")
    
    # Return current rates (may be defaults if fetch failed)
    result = dict(DEFAULT_EXCHANGE_RATES)
    result.update(_exchange_rate_cache)
    
    return result


def get_cached_rates() -> dict:
    """
    Get currently cached exchange rates.
    
    Returns:
        dict: Current exchange rates
    """
    result = dict(DEFAULT_EXCHANGE_RATES)
    result.update(_exchange_rate_cache)
    return result


# Suppress noisy third-party logging on module import
_suppress_noisy_logging()
