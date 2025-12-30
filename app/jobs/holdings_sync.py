"""
Holdings Sync Job

Synchronizes SnapTrade holdings to the Position table for AUM tracking.
Runs every 4 hours before portfolio snapshots to ensure accurate data.
"""

from app.models.database import SessionLocal, User, Connection, Position
from app.services.snaptrade_integration import SnapTradeClient
from app.core.currency import convert_to_cad
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging
import uuid

logger = logging.getLogger(__name__)


def _get_asset_class(symbol: str, broker: str) -> str:
    """Determine asset class based on symbol and broker."""
    # Crypto symbols (common ones)
    crypto_symbols = {
        'BTC', 'ETH', 'SOL', 'ATOM', 'AVAX', 'DOT', 'LINK', 'MATIC', 
        'ADA', 'XRP', 'DOGE', 'LTC', 'BCH', 'UNI', 'AAVE', 'CRV',
        'NEAR', 'FTM', 'ALGO', 'XLM', 'VET', 'EOS', 'TRX', 'XTZ',
        'KSM', 'ATOM07', 'KSM07', 'DOT07',  # Staking variants
    }
    
    # Stablecoins and cash equivalents
    cash_symbols = {'USDT', 'USDC', 'DAI', 'BUSD', 'UST', 'FRAX', 'USD', 'CAD', 'EUR'}
    
    # Clean symbol (remove .CC suffix for Kraken, strip numbers for staking variants)
    clean_symbol = symbol.upper().split('.')[0]
    base_symbol = ''.join(c for c in clean_symbol if not c.isdigit())
    
    # Check if it's a crypto broker
    if broker.lower() in ('kraken', 'coinbase', 'binance', 'crypto.com'):
        if base_symbol in cash_symbols or clean_symbol in cash_symbols:
            return 'cash'
        return 'crypto'
    
    # Check if symbol is a known crypto
    if base_symbol in crypto_symbols or clean_symbol in crypto_symbols:
        return 'crypto'
    
    # Check for stablecoin/cash patterns
    if base_symbol in cash_symbols or clean_symbol in cash_symbols:
        return 'cash'
    
    # Default to stocks for other symbols
    return 'stocks'


def sync_user_holdings_sync(user_id: str, db) -> Dict[str, any]:
    """
    Sync holdings for a single user from all their connected accounts (synchronous version).
    
    Args:
        user_id: User ID to sync holdings for
        db: Database session
    
    Returns:
        Dict with counts: {'synced': int, 'errors': int, 'total_value': float}
    """
    result = {'synced': 0, 'errors': 0, 'total_value': 0.0}
    
    try:
        # Get all active connections for this user
        connections = db.query(Connection).filter(
            Connection.user_id == user_id,
            Connection.is_connected == True
        ).all()
        
        if not connections:
            logger.debug(f"No active connections for user {user_id}")
            return result
        
        # Collect all holdings from all accounts
        all_holdings: Dict[str, Dict] = {}  # symbol -> holding data
        total_cash = 0.0
        
        for connection in connections:
            if not connection.snaptrade_user_id or not connection.snaptrade_user_secret:
                logger.debug(f"Skipping connection {connection.id} - missing credentials")
                continue
                
            try:
                # Initialize SnapTrade client for this connection
                client = SnapTradeClient(
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret
                )
                
                # Get all holdings with balances
                holdings_data = client.get_all_holdings_with_balances()
                
                for holding in holdings_data.get('holdings', []):
                    symbol = holding['symbol'].upper()
                    asset_class = _get_asset_class(symbol, holding.get('broker', connection.broker))
                    
                    # Convert to CAD if needed
                    currency = holding.get('currency', 'CAD')
                    market_value = holding.get('market_value', 0)
                    price = holding.get('price', 0)
                    
                    if currency != 'CAD':
                        market_value = convert_to_cad(market_value, currency)
                        price = convert_to_cad(price, currency)
                    
                    # Aggregate holdings by symbol
                    if symbol in all_holdings:
                        all_holdings[symbol]['quantity'] += holding.get('quantity', 0)
                        all_holdings[symbol]['market_value'] += market_value
                        if price > 0:
                            all_holdings[symbol]['price'] = price
                    else:
                        all_holdings[symbol] = {
                            'symbol': symbol,
                            'name': holding.get('name', symbol),
                            'quantity': holding.get('quantity', 0),
                            'price': price,
                            'market_value': market_value,
                            'currency': 'CAD',  # Normalized
                            'asset_class': asset_class,
                            'broker': holding.get('broker', connection.broker),
                        }
                
                # Add cash from this connection
                total_cash += holdings_data.get('cash_value', 0)
                
                # Update connection balance
                connection.account_balance = holdings_data.get('total_value', 0)
                connection.updated_at = datetime.now(timezone.utc)
                
                logger.debug(f"Fetched {len(holdings_data.get('holdings', []))} holdings from {connection.broker} for user {user_id}")
                
            except Exception as e:
                logger.error(f"Failed to fetch holdings from {connection.broker} for user {user_id}: {e}")
                result['errors'] += 1
                continue
        
        # Add cash as a position if significant
        if total_cash > 0:
            all_holdings['CASH_CAD'] = {
                'symbol': 'CASH_CAD',
                'name': 'Cash (CAD)',
                'quantity': total_cash,
                'price': 1.0,
                'market_value': total_cash,
                'currency': 'CAD',
                'asset_class': 'cash',
                'broker': 'aggregate',
            }
        
        # Calculate total portfolio value
        total_value = sum(h['market_value'] for h in all_holdings.values())
        result['total_value'] = total_value
        
        # Update positions in database
        existing_positions = {p.symbol: p for p in db.query(Position).filter(Position.user_id == user_id).all()}
        
        # Update or create positions
        for symbol, holding_data in all_holdings.items():
            allocation_pct = (holding_data['market_value'] / total_value * 100) if total_value > 0 else 0
            
            if symbol in existing_positions:
                # Update existing position
                position = existing_positions[symbol]
                position.quantity = holding_data['quantity']
                position.price = holding_data['price']
                position.market_value = holding_data['market_value']
                position.allocation_percentage = allocation_pct
                position.updated_at = datetime.now(timezone.utc)
                if position.metadata_json is None:
                    position.metadata_json = {}
                position.metadata_json['asset_class'] = holding_data['asset_class']
                position.metadata_json['currency'] = holding_data['currency']
                position.metadata_json['name'] = holding_data['name']
            else:
                # Create new position
                position = Position(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    symbol=symbol,
                    quantity=holding_data['quantity'],
                    price=holding_data['price'],
                    market_value=holding_data['market_value'],
                    allocation_percentage=allocation_pct,
                    target_percentage=0.0,
                    metadata_json={
                        'asset_class': holding_data['asset_class'],
                        'currency': holding_data['currency'],
                        'name': holding_data['name'],
                    }
                )
                db.add(position)
            
            result['synced'] += 1
        
        # Remove positions that no longer exist
        for symbol, position in existing_positions.items():
            if symbol not in all_holdings:
                db.delete(position)
                logger.debug(f"Removed stale position {symbol} for user {user_id}")
        
        db.commit()
        logger.info(f"Synced {result['synced']} positions for user {user_id}, total value: ${total_value:,.2f}")
        
    except Exception as e:
        logger.error(f"Failed to sync holdings for user {user_id}: {e}", exc_info=True)
        db.rollback()
        result['errors'] += 1
    
    return result


async def sync_user_holdings(user_id: str, db) -> Dict[str, any]:
    """Async wrapper for sync_user_holdings_sync."""
    return sync_user_holdings_sync(user_id, db)


def sync_all_holdings_sync():
    """
    Sync holdings for all active users (synchronous version).
    """
    db = SessionLocal()
    try:
        # Get all user IDs that have connections, then load users separately
        # This avoids the DISTINCT issue with JSON columns
        user_ids_with_connections = db.query(Connection.user_id).filter(
            Connection.is_connected == True
        ).distinct().all()
        
        user_ids = [uid[0] for uid in user_ids_with_connections]
        
        if not user_ids:
            logger.info("No users with connections found")
            return {'users_processed': 0, 'positions_synced': 0, 'total_aum': 0, 'errors': 0}
        
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        
        total_synced = 0
        total_errors = 0
        total_aum = 0.0
        users_processed = 0
        
        for user in users:
            result = sync_user_holdings_sync(user.id, db)
            total_synced += result['synced']
            total_errors += result['errors']
            total_aum += result['total_value']
            users_processed += 1
        
        logger.info(
            f"[OK] Holdings sync completed: {users_processed} users, "
            f"{total_synced} positions, ${total_aum:,.2f} AUM, {total_errors} errors"
        )
        
        return {
            'users_processed': users_processed,
            'positions_synced': total_synced,
            'total_aum': total_aum,
            'errors': total_errors,
        }
        
    except Exception as e:
        logger.error(f"Holdings sync job failed: {e}", exc_info=True)
        db.rollback()
        return {'users_processed': 0, 'positions_synced': 0, 'total_aum': 0, 'errors': 1}
    finally:
        db.close()


async def sync_all_holdings():
    """
    Sync holdings for all active users.
    Called every 4 hours by APScheduler (before portfolio snapshots).
    """
    return sync_all_holdings_sync()
