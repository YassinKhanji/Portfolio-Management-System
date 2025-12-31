"""
Holdings Sync Job

Synchronizes SnapTrade holdings to the Position table for AUM tracking.
Runs every 4 hours before portfolio snapshots to ensure accurate data.
"""

from app.models.database import SessionLocal, User, Connection, Position, Transaction
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
            logger.debug("No active connections; skipping user holdings sync")
            return result
        
        # Collect all holdings from all accounts
        all_holdings: Dict[str, Dict] = {}  # symbol -> holding data
        total_cash = 0.0
        
        for connection in connections:
            if not connection.snaptrade_user_id or not connection.snaptrade_user_secret:
                logger.debug("Skipping connection - missing credentials")
                continue
                
            try:
                # Initialize SnapTrade client for this connection
                client = SnapTradeClient(
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret
                )
                
                # Get all holdings with balances (includes average_purchase_price)
                holdings_data = client.get_all_holdings_with_balances()
                
                for holding in holdings_data.get('holdings', []):
                    symbol = holding['symbol'].upper()
                    asset_class = _get_asset_class(symbol, holding.get('broker', connection.broker))
                    
                    # Convert to CAD if needed
                    currency = holding.get('currency', 'CAD')
                    market_value = holding.get('market_value', 0)
                    price = holding.get('price', 0)
                    avg_purchase_price = holding.get('average_purchase_price', 0)
                    
                    if currency != 'CAD':
                        market_value = convert_to_cad(market_value, currency)
                        price = convert_to_cad(price, currency)
                        if avg_purchase_price:
                            avg_purchase_price = convert_to_cad(avg_purchase_price, currency)
                    
                    # Aggregate holdings by symbol
                    if symbol in all_holdings:
                        all_holdings[symbol]['quantity'] += holding.get('quantity', 0)
                        all_holdings[symbol]['market_value'] += market_value
                        if price > 0:
                            all_holdings[symbol]['price'] = price
                        # Keep the first non-zero avg_purchase_price
                        if avg_purchase_price and not all_holdings[symbol].get('average_purchase_price'):
                            all_holdings[symbol]['average_purchase_price'] = avg_purchase_price
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
                            'average_purchase_price': avg_purchase_price,
                        }
                
                # Add cash from this connection
                total_cash += holdings_data.get('cash_value', 0)
                
                # Update connection balance
                connection.account_balance = holdings_data.get('total_value', 0)
                connection.updated_at = datetime.now(timezone.utc)
                
                logger.debug("Fetched holdings from broker")
                
            except Exception as e:
                logger.error("Failed to fetch holdings from broker", exc_info=True)
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
        
        # Fetch last orders by symbol across all connections
        last_orders_by_symbol: Dict[str, Dict] = {}
        for connection in connections:
            if not connection.snaptrade_user_id or not connection.snaptrade_user_secret:
                continue
            try:
                client = SnapTradeClient(
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret
                )
                orders = client.get_last_orders_by_symbol(days=90)
                # Merge, keeping the most recent order for each symbol
                for symbol, order_data in orders.items():
                    if symbol not in last_orders_by_symbol:
                        last_orders_by_symbol[symbol] = order_data
                    else:
                        existing_time = last_orders_by_symbol[symbol].get('time_placed')
                        new_time = order_data.get('time_placed')
                        if new_time and (not existing_time or new_time > existing_time):
                            last_orders_by_symbol[symbol] = order_data
            except Exception as e:
                logger.warning(f"Failed to fetch orders from {connection.broker}: {e}")
                continue
        
        logger.info("Fetched recent orders for cost basis")
        
        # Update positions in database
        existing_positions = {p.symbol: p for p in db.query(Position).filter(Position.user_id == user_id).all()}
        
        # Update or create positions
        for symbol, holding_data in all_holdings.items():
            allocation_pct = (holding_data['market_value'] / total_value * 100) if total_value > 0 else 0
            
            # Get cost basis from average_purchase_price (already converted to CAD)
            symbol_cost_basis = holding_data.get('average_purchase_price', 0) or None
            
            # If no cost basis from holdings, try to calculate from order data
            last_order = last_orders_by_symbol.get(symbol, {})
            if not symbol_cost_basis and last_order:
                order_price = last_order.get('price')
                if order_price and order_price > 0:
                    # Order prices from SnapTrade are already in the account's currency (CAD for Kraken)
                    # No conversion needed - use the price as-is
                    symbol_cost_basis = order_price
            
            # Get last order info for this symbol
            last_order = last_orders_by_symbol.get(symbol, {})
            last_order_time = last_order.get('time_placed')
            last_order_side = last_order.get('action', 'HOLD')  # Default to HOLD if no recent order
            
            if symbol in existing_positions:
                # Update existing position
                position = existing_positions[symbol]
                position.quantity = holding_data['quantity']
                position.price = holding_data['price']
                position.market_value = holding_data['market_value']
                position.allocation_percentage = allocation_pct
                position.updated_at = datetime.now(timezone.utc)
                if symbol_cost_basis:
                    position.cost_basis = symbol_cost_basis
                if last_order_time:
                    position.last_order_time = last_order_time
                if last_order_side:
                    position.last_order_side = last_order_side
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
                    cost_basis=symbol_cost_basis,
                    last_order_time=last_order_time,
                    last_order_side=last_order_side or 'HOLD',
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
        
        db.commit()
        logger.info("Holdings sync complete")
        
    except Exception as e:
        logger.error("Failed to sync holdings", exc_info=True)
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
        
        logger.info("[OK] Holdings sync completed")
        
        return {
            'users_processed': users_processed,
            'positions_synced': total_synced,
            'total_aum': total_aum,
            'errors': total_errors,
        }
        
    except Exception as e:
        logger.error("Holdings sync job failed", exc_info=True)
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


def sync_user_transactions_sync(user_id: str, db, days: int = 30) -> Dict[str, any]:
    """
    Sync orders from SnapTrade to the Transaction table for a single user.
    
    Args:
        user_id: User ID to sync transactions for
        db: Database session
        days: Number of days to look back for orders
    
    Returns:
        Dict with counts: {'synced': int, 'errors': int}
    """
    result = {'synced': 0, 'errors': 0}
    
    try:
        # Get all active connections for this user
        connections = db.query(Connection).filter(
            Connection.user_id == user_id,
            Connection.is_connected == True
        ).all()
        
        if not connections:
            logger.debug("No active connections; skipping user transaction sync")
            return result
        
        # Get existing transaction IDs to avoid duplicates
        existing_order_ids = set(
            t.snaptrade_order_id for t in 
            db.query(Transaction.snaptrade_order_id).filter(
                Transaction.user_id == user_id,
                Transaction.snaptrade_order_id.isnot(None)
            ).all()
        )
        
        for connection in connections:
            if not connection.snaptrade_user_id or not connection.snaptrade_user_secret:
                continue
                
            try:
                client = SnapTradeClient(
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret
                )
                
                # Get all orders from all accounts
                orders = client.get_all_account_orders(days=days)
                
                for order in orders:
                    brokerage_order_id = order.get('brokerage_order_id')
                    
                    # Skip if already synced or not executed
                    if brokerage_order_id in existing_order_ids:
                        continue
                    
                    status = order.get('status', '').upper()
                    if status not in ('EXECUTED', 'FILLED', 'COMPLETE', 'COMPLETED'):
                        continue
                    
                    # Extract symbol
                    universal_symbol = order.get('universal_symbol', {})
                    if isinstance(universal_symbol, dict):
                        symbol = universal_symbol.get('symbol', universal_symbol.get('raw_symbol', ''))
                    else:
                        symbol = str(universal_symbol) if universal_symbol else ''
                    
                    if not symbol:
                        continue
                    
                    # Parse order data
                    action = order.get('action', 'BUY').upper()
                    quantity = float(order.get('filled_quantity', order.get('total_quantity', 0)) or 0)
                    execution_price = float(order.get('execution_price', 0) or 0)
                    
                    # Parse timestamps
                    time_placed = order.get('time_placed') or order.get('time_executed')
                    executed_at = None
                    if time_placed:
                        if isinstance(time_placed, str):
                            try:
                                executed_at = datetime.fromisoformat(time_placed.replace('Z', '+00:00'))
                            except ValueError:
                                executed_at = datetime.now(timezone.utc)
                        elif isinstance(time_placed, datetime):
                            executed_at = time_placed
                    
                    # Create transaction record
                    transaction = Transaction(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        symbol=symbol.upper(),
                        quantity=quantity,
                        price=execution_price,
                        side=action,
                        snaptrade_order_id=brokerage_order_id,
                        status='filled',
                        created_at=executed_at or datetime.now(timezone.utc),
                        executed_at=executed_at,
                        metadata_json={
                            'broker': connection.broker,
                            'order_type': order.get('order_type'),
                            'time_in_force': order.get('time_in_force'),
                        }
                    )
                    
                    db.add(transaction)
                    existing_order_ids.add(brokerage_order_id)
                    result['synced'] += 1
                    logger.debug("Synced transaction")
                    
            except Exception as e:
                logger.error("Failed to fetch orders from broker", exc_info=True)
                result['errors'] += 1
                continue
        
        db.commit()
        logger.info("Transaction sync complete")
        
    except Exception as e:
        logger.error("Failed to sync transactions", exc_info=True)
        db.rollback()
        result['errors'] += 1
    
    return result


def sync_all_transactions_sync(days: int = 30):
    """
    Sync transactions for all active users.
    """
    db = SessionLocal()
    try:
        user_ids_with_connections = db.query(Connection.user_id).filter(
            Connection.is_connected == True
        ).distinct().all()
        
        user_ids = [uid[0] for uid in user_ids_with_connections]
        
        if not user_ids:
            logger.info("No users with connections found for transaction sync")
            return {'users_processed': 0, 'transactions_synced': 0, 'errors': 0}
        
        total_synced = 0
        total_errors = 0
        users_processed = 0
        
        for uid in user_ids:
            result = sync_user_transactions_sync(uid, db, days=days)
            total_synced += result['synced']
            total_errors += result['errors']
            users_processed += 1
        
        logger.info("[OK] Transaction sync completed")
        
        return {
            'users_processed': users_processed,
            'transactions_synced': total_synced,
            'errors': total_errors,
        }
        
    except Exception as e:
        logger.error("Transaction sync job failed", exc_info=True)
        db.rollback()
        return {'users_processed': 0, 'transactions_synced': 0, 'errors': 1}
    finally:
        db.close()


async def sync_all_transactions(days: int = 30):
    """
    Sync transactions for all active users.
    Called after holdings sync.
    """
    return sync_all_transactions_sync(days=days)
