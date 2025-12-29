"""Trade Executor Module

Execute trades via SnapTrade API using per-connection credentials and account types.
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.database import Connection
from app.services.snaptrade_integration import SnapTradeClient, SnapTradeClientError
from app.jobs.utils import is_emergency_stop_active

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Execute trades via SnapTrade with per-account-type routing."""
    
    def __init__(self, connections: List[Connection]):
        """Initialize executor with all available SnapTrade connections for a user."""
        self.connections = {c.account_type.lower(): c for c in connections}
        self.clients: Dict[str, SnapTradeClient] = {}
        for acct_type, conn in self.connections.items():
            self.clients[acct_type] = SnapTradeClient(
                user_id=conn.snaptrade_user_id,
                user_secret=conn.snaptrade_user_secret,
            )

    def _pick_connection(self, trade_info: Dict) -> Connection:
        """Select connection based on trade metadata (account_type/asset_class)."""
        acct_type = str(trade_info.get("account_type") or trade_info.get("asset_class") or "").lower()
        if acct_type in {"crypto", "cryptocurrency"}:
            key = "crypto"
        elif acct_type in {"equities", "equity", "stock", "stocks"}:
            key = "equities"
        else:
            key = "equities"  # default route

        conn = self.connections.get(key)
        if not conn:
            raise SnapTradeClientError(f"No SnapTrade connection configured for account_type='{key}'")
        if not conn.account_id:
            raise SnapTradeClientError(f"SnapTrade connection '{key}' is missing account_id")
        return conn
    
    def execute_trades(
        self,
        trades: Dict,
    ) -> List[Dict]:
        """
        Execute all required trades
        
        Args:
            trades: Dict of trades to execute {symbol: {action, quantity, account_type?, ...}}
        
        Returns:
            List of executed trades with results
        """
        executed = []
        
        try:
            if is_emergency_stop_active():
                logger.error("Emergency stop active; aborting trade execution")
                raise RuntimeError("Trading halted: emergency_stop is active")

            logger.info(f"Executing {len(trades)} trades with account-type routing...")
            
            for symbol, trade_info in trades.items():
                try:
                    conn = self._pick_connection(trade_info)
                    client = self.clients[conn.account_type.lower()]
                    account_id = conn.account_id

                    action = trade_info['action']
                    quantity = trade_info['quantity']
                    
                    if action == "BUY":
                        result = client.buy(account_id, symbol, quantity)
                    elif action == "SELL":
                        result = client.sell(account_id, symbol, quantity)
                    else:
                        logger.warning(f"Unknown action: {action}")
                        continue
                    
                    executed.append({
                        "symbol": symbol,
                        "action": action,
                        "quantity": quantity,
                        "status": result.status,
                        "order_id": result.order_id
                    })
                    
                    logger.info(f"Executed: {action} {quantity} {symbol}")
                
                except SnapTradeClientError as e:
                    logger.error(f"Trade failed for {symbol}: {str(e)}")
                    executed.append({
                        "symbol": symbol,
                        "action": action,
                        "status": "FAILED",
                        "error": str(e)
                    })
        
        except Exception as e:
            logger.error(f"Trade execution error: {str(e)}")
            raise
        
        logger.info(f"Trade execution complete: {len(executed)} trades")
        return executed
    
    def get_current_holdings(self, account_id: str = None) -> Dict:
        """Get current portfolio holdings"""
        try:
            holdings = self.client.get_holdings(account_id)
            
            return {
                h.symbol: {
                    "quantity": h.quantity,
                    "price": h.price,
                    "market_value": h.market_value
                }
                for h in holdings
            }
        
        except SnapTradeClientError as e:
            logger.error(f"Failed to get holdings: {str(e)}")
            raise
    
    def cancel_trade(self, account_id: str, order_id: str) -> bool:
        """Cancel a pending order"""
        try:
            if is_emergency_stop_active():
                logger.error("Emergency stop active; cancel order skipped")
                raise RuntimeError("Trading halted: emergency_stop is active")
            return self.client.cancel_order(account_id, order_id)
        except SnapTradeClientError as e:
            logger.error(f"Failed to cancel order: {str(e)}")
            raise
