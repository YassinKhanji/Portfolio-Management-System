"""Test script to check Kraken holdings data from SnapTrade"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.services.snaptrade_integration import SnapTradeClient
from app.models.database import SessionLocal, User, Connection

def main():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == 'yassinkhanji@gmail.com').first()
        if not user:
            print("User not found")
            return
        print(f"User: {user.email}")

        conn = db.query(Connection).filter(
            Connection.user_id == user.id, 
            Connection.broker == 'kraken'
        ).first()
        
        if not conn:
            print("Kraken connection not found")
            return
            
        print(f"Kraken connected: {conn.is_connected}")

        client = SnapTradeClient(conn.snaptrade_user_id, conn.snaptrade_user_secret)
        accounts = client.get_accounts()
        print(f"\nFound {len(accounts)} accounts")

        for acct in accounts:
            print(f"\n=== Account: {acct.name} ({acct.id}) ===")
            holdings_result = client.get_holdings(acct.id)
            
            print(f"\nHoldings ({len(holdings_result.holdings)}):")
            for h in holdings_result.holdings:
                print(f"  {h.symbol}:")
                print(f"    Quantity: {h.quantity}")
                print(f"    Current Price: {h.price} {h.currency}")
                print(f"    Avg Purchase Price: {h.average_purchase_price} {h.currency}")
                print(f"    Market Value: {h.market_value} {h.currency}")
                
                # Calculate expected return
                if h.average_purchase_price and h.average_purchase_price > 0:
                    expected_return = ((h.price - h.average_purchase_price) / h.average_purchase_price) * 100
                    print(f"    Expected Return: {expected_return:.2f}%")
                else:
                    print(f"    Expected Return: N/A (no avg price)")
                print()
        
        # Also check order history
        print("\n=== Order History ===")
        orders_by_symbol = client.get_last_orders_by_symbol(days=365)
        for symbol, data in orders_by_symbol.items():
            print(f"  {symbol}: avg_buy_price={data.get('avg_buy_price', 'N/A')}, last_action={data.get('action')}, time={data.get('time_placed')}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
