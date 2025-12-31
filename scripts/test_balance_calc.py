"""
Test script to verify balance calculation is correct after currency fix.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.market_data import get_live_crypto_prices
from app.core.currency import convert_to_cad

def test_live_crypto_prices():
    """Test that live crypto prices are returned in CAD."""
    print("=" * 60)
    print("Testing Live Crypto Prices (should be in CAD)")
    print("=" * 60)
    
    symbols = ["XETH", "XXDG", "KSM", "ATOM", "DOT"]
    prices = get_live_crypto_prices(symbols)
    
    print(f"\nReturned prices: {prices}")
    
    # Expected: ETH around 4000-5000 CAD, DOGE around 0.45-0.55 CAD
    if "XETH" in prices:
        eth_price = prices["XETH"]
        print(f"\nXETH price: {eth_price:.2f} CAD")
        # ETH should be between 3000-6000 CAD
        if 3000 <= eth_price <= 6000:
            print("✓ ETH price is in reasonable CAD range")
        else:
            print(f"✗ ETH price {eth_price} seems wrong for CAD")
    
    if "XXDG" in prices:
        doge_price = prices["XXDG"]
        print(f"\nXXDG price: {doge_price:.6f} CAD")
        # DOGE should be between 0.30-0.70 CAD
        if 0.30 <= doge_price <= 0.70:
            print("✓ DOGE price is in reasonable CAD range")
        else:
            print(f"✗ DOGE price {doge_price} seems wrong for CAD")
    
    return prices


def test_conversion_no_double():
    """Test that converting CAD to CAD doesn't change the value."""
    print("\n" + "=" * 60)
    print("Testing Currency Conversion (CAD -> CAD should be identity)")
    print("=" * 60)
    
    test_value = 4500.12345
    result = convert_to_cad(test_value, "CAD")
    
    print(f"\nInput: {test_value}")
    print(f"Output: {result}")
    print(f"Equal: {test_value == result}")
    
    if test_value == result:
        print("✓ CAD to CAD conversion is identity (no change)")
    else:
        print("✗ CAD to CAD conversion changed the value!")


def simulate_holdings_calculation():
    """Simulate the holdings calculation flow to verify correctness."""
    print("\n" + "=" * 60)
    print("Simulating Holdings Calculation")
    print("=" * 60)
    
    # Get live prices (already in CAD)
    prices_cad = get_live_crypto_prices(["XETH", "XXDG"])
    
    # Simulated holdings
    holdings = [
        {"symbol": "XETH", "quantity": 0.003735631, "currency": "CAD"},  # ETH
        {"symbol": "XXDG", "quantity": 20.0, "currency": "CAD"},  # DOGE
        {"symbol": "SHOP.TO", "quantity": 0.0371, "price_cad": 224.21, "currency": "CAD"},  # Already in CAD
        {"symbol": "CAD", "quantity": 21.5551, "price_cad": 1.0, "currency": "CAD"},  # Cash
    ]
    
    total = 0.0
    print("\nPosition-by-position breakdown:")
    
    for h in holdings:
        symbol = h["symbol"]
        qty = h["quantity"]
        
        if symbol in prices_cad:
            # Live price from CCXT (already in CAD)
            price = prices_cad[symbol]
            currency = "CAD"
        else:
            price = h.get("price_cad", 0)
            currency = h.get("currency", "CAD")
        
        # Convert to CAD if needed (should be no-op for CAD)
        price_cad = convert_to_cad(price, currency)
        market_value = qty * price_cad
        
        print(f"  {symbol}: {qty} @ {price_cad:.4f} CAD = {market_value:.2f} CAD")
        total += market_value
    
    print(f"\n  TOTAL: {total:.2f} CAD")
    
    # Expected total should be around 50-55 CAD based on user's info
    if 45 <= total <= 60:
        print("✓ Total is in expected range (~$50 CAD)")
    else:
        print(f"✗ Total {total:.2f} seems wrong (expected ~$50)")


if __name__ == "__main__":
    test_live_crypto_prices()
    test_conversion_no_double()
    simulate_holdings_calculation()
    print("\n" + "=" * 60)
    print("Tests completed")
    print("=" * 60)
