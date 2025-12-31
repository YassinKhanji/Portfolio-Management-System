"""Test live crypto price fetching and return calculation"""
import sys
sys.path.insert(0, ".")

from app.services.market_data import get_live_crypto_prices
from app.models.database import SessionLocal, Position

def main():
    db = SessionLocal()
    try:
        xeth = db.query(Position).filter(Position.symbol == 'XETH').first()
        xxdg = db.query(Position).filter(Position.symbol == 'XXDG').first()
        
        prices = get_live_crypto_prices(['XETH', 'XXDG'])
        
        print("=== XETH ===")
        eth_price = prices.get('XETH')
        print(f"  Live price: {eth_price} CAD")
        print(f"  Cost basis: {xeth.cost_basis} CAD")
        if eth_price and xeth.cost_basis:
            ret_eth = ((eth_price - xeth.cost_basis) / xeth.cost_basis) * 100
            print(f"  Return: {ret_eth:.2f}%")
        print(f"  Expected: +1.13%")
        
        print("\n=== XXDG ===")
        doge_price = prices.get('XXDG')
        print(f"  Live price: {doge_price} CAD")
        print(f"  Cost basis: {xxdg.cost_basis} CAD")
        if doge_price and xxdg.cost_basis:
            ret_doge = ((doge_price - xxdg.cost_basis) / xxdg.cost_basis) * 100
            print(f"  Return: {ret_doge:.2f}%")
        print(f"  Expected: -0.23%")
    finally:
        db.close()

if __name__ == "__main__":
    main()
