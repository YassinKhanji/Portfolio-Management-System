"""Trade Executor Module

Execute trades via SnapTrade API using per-connection credentials and account types.
"""

import logging
from typing import Dict, List, Optional, Tuple

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

    def _normalize_account_key(self, raw: str) -> str:
        acct_type = str(raw or "").lower()
        if acct_type in {"crypto", "cryptocurrency"}:
            return "crypto"
        if acct_type in {"equities", "equity", "stock", "stocks"}:
            return "equities"
        return "equities"

    def _get_connection_client_account_id(
        self,
        *,
        account_id: Optional[str] = None,
        account_type: Optional[str] = None,
    ) -> Tuple[Connection, SnapTradeClient, str]:
        """Resolve a connection + client + account_id for non-trade calls.

        - If account_id is provided, it must match a known connection.
        - Else if account_type is provided, it routes using the same mapping as trade execution.
        - Else defaults to equities.
        """

        conn: Optional[Connection] = None
        key: Optional[str] = None

        if account_id:
            for k, candidate in self.connections.items():
                if candidate.account_id == account_id:
                    conn = candidate
                    key = k
                    break
            if not conn:
                raise SnapTradeClientError("Unknown account_id for TradeExecutor")
        else:
            key = self._normalize_account_key(account_type or "equities")
            conn = self.connections.get(key)
            if not conn:
                raise SnapTradeClientError(f"No SnapTrade connection configured for account_type='{key}'")
            account_id = conn.account_id

        if not account_id:
            raise SnapTradeClientError("SnapTrade connection is missing account_id")

        client = self.clients.get(key or "")
        if not client:
            # Defensive: if clients dict got out of sync with connections.
            client = SnapTradeClient(
                user_id=conn.snaptrade_user_id,
                user_secret=conn.snaptrade_user_secret,
            )
            if key:
                self.clients[key] = client

        return conn, client, account_id

    def _pick_connection(self, trade_info: Dict) -> Connection:
        """Select connection based on trade metadata (account_type/asset_class)."""
        acct_type = str(trade_info.get("account_type") or trade_info.get("asset_class") or "").lower()
        key = self._normalize_account_key(acct_type)

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

            logger.info("Executing trades")
            
            for symbol, trade_info in trades.items():
                try:
                    conn = self._pick_connection(trade_info)
                    key = conn.account_type.lower()
                    client = self.clients.get(key)
                    if not client:
                        client = SnapTradeClient(
                            user_id=conn.snaptrade_user_id,
                            user_secret=conn.snaptrade_user_secret,
                        )
                        self.clients[key] = client
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
                    
                    logger.info("Trade executed")
                
                except SnapTradeClientError as e:
                    logger.error("Trade failed", exc_info=True)
                    executed.append({
                        "symbol": symbol,
                        "action": action,
                        "status": "FAILED",
                        "error": str(e)
                    })
        
        except Exception as e:
            logger.error("Trade execution error", exc_info=True)
            raise
        
        logger.info("Trade execution complete")
        return executed
    
    def get_current_holdings(self, account_id: str = None) -> Dict:
        """Get current portfolio holdings"""
        try:
            _, client, resolved_account_id = self._get_connection_client_account_id(account_id=account_id)
            holdings = client.get_holdings(resolved_account_id)
            
            return {
                h.symbol: {
                    "quantity": h.quantity,
                    "price": h.price,
                    "market_value": h.market_value
                }
                for h in holdings
            }
        
        except SnapTradeClientError as e:
            logger.error("Failed to get holdings", exc_info=True)
            raise
    
    def cancel_trade(self, account_id: str, order_id: str) -> bool:
        """Cancel a pending order"""
        try:
            if is_emergency_stop_active():
                logger.error("Emergency stop active; cancel order skipped")
                raise RuntimeError("Trading halted: emergency_stop is active")
            _, client, resolved_account_id = self._get_connection_client_account_id(account_id=account_id)
            return client.cancel_order(resolved_account_id, order_id)
        except SnapTradeClientError as e:
            logger.error("Failed to cancel order", exc_info=True)
            raise
