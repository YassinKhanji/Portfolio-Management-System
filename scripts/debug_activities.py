"""Debug script to check SnapTrade activities for cost basis calculation."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.database import SessionLocal, Connection
from app.services.snaptrade_integration import SnapTradeClient

db = SessionLocal()

# Check all connections
connections = db.query(Connection).filter(Connection.is_connected == True).all()
for conn in connections:
    print(f"\n{'='*50}")
    print(f"Connection: {conn.broker}")
    print(f"User ID: {conn.snaptrade_user_id}")
    
    client = SnapTradeClient(conn.snaptrade_user_id, conn.snaptrade_user_secret)
    
    # Get accounts first
    accounts = client.get_accounts()
    print(f"Accounts: {len(accounts)}")
    for account in accounts:
        print(f"  - {account.name} ({account.id})")
    
    # Get activities
    activities = client.get_all_account_activities()
    print(f"Activities count: {len(activities)}")
    
    for act in activities[:10]:
        act_type = act.get('type', 'N/A')
        symbol = act.get('symbol')
        if symbol and isinstance(symbol, dict):
            symbol = symbol.get('symbol', symbol.get('raw_symbol', 'N/A'))
        price = act.get('price', 0)
        units = act.get('units', 0)
        print(f"  Type: {act_type}, Symbol: {symbol}, Price: {price}, Units: {units}")
    
    # Calculate cost basis
    cost_basis = client.calculate_cost_basis(activities)
    print(f"Cost basis calculated for {len(cost_basis)} symbols:")
    for symbol, basis in cost_basis.items():
        print(f"  {symbol}: ${basis:.4f}")

db.close()
