"""
Trade Executor Module

Execute trades via SnapTrade API.
"""

from app.services.snaptrade_integration import SnapTradeClient, SnapTradeClientError
from app.jobs.utils import is_emergency_stop_active
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Execute trades via SnapTrade"""
    
    def __init__(self, user_snaptrade_token: str):
        """Initialize executor with user's SnapTrade token"""
        self.client = SnapTradeClient(user_token=user_snaptrade_token)
    
    def execute_trades(
        self,
        account_id: str,
        trades: Dict
    ) -> List[Dict]:
        """
        Execute all required trades
        
        Args:
            account_id: SnapTrade account ID
            trades: Dict of trades to execute {symbol: {action, quantity, ...}}
        
        Returns:
            List of executed trades with results
        """
        executed = []
        
        try:
            if is_emergency_stop_active():
                logger.error("Emergency stop active; aborting trade execution")
                raise RuntimeError("Trading halted: emergency_stop is active")

            logger.info(f"Executing {len(trades)} trades for account {account_id}...")
            
            for symbol, trade_info in trades.items():
                try:
                    action = trade_info['action']
                    quantity = trade_info['quantity']
                    
                    if action == "BUY":
                        result = self.client.buy(account_id, symbol, quantity)
                    elif action == "SELL":
                        result = self.client.sell(account_id, symbol, quantity)
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
