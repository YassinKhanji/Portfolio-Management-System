"""
Portfolio Calculator Module

Calculate optimal portfolio allocations based on market regime.
"""

from allocation import AllocationStrategy, REGIME_OBJECTIVES, REGIME_CONSTRAINTS
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class PortfolioCalculator:
    """Portfolio allocation calculator"""
    
    def __init__(self):
        self.strategy = AllocationStrategy()
    
    def calculate_target_allocation(
        self,
        risk_profile: str,
        regime: str,
        total_value: float
    ) -> Dict[str, float]:
        """
        Calculate target allocation based on regime and risk profile
        
        Args:
            risk_profile: Conservative, Balanced, Aggressive
            regime: BULL, BEAR, SIDEWAYS, HODL
            total_value: Total portfolio value
        
        Returns:
            Dict with symbol -> target_amount
        """
        try:
            logger.info(f"Calculating allocation: {regime}/{risk_profile}")
            
            # Get regime-based targets
            targets = REGIME_OBJECTIVES.get(regime, REGIME_OBJECTIVES['HODL'])
            
            # Adjust for risk profile
            targets = self._adjust_for_risk(targets, risk_profile)
            
            # Convert percentages to amounts
            allocation = {
                symbol: pct * total_value
                for symbol, pct in targets.items()
            }
            
            logger.info(f"Allocation calculated: {targets}")
            return allocation
        
        except Exception as e:
            logger.error(f"Allocation calculation failed: {str(e)}")
            raise
    
    def _adjust_for_risk(self, targets: Dict, risk_profile: str) -> Dict:
        """Adjust allocation targets based on risk profile"""
        if risk_profile == "Conservative":
            # Reduce crypto, increase stable
            return {
                "BTC": targets.get("BTC", 0.3) * 0.5,
                "ETH": targets.get("ETH", 0.2) * 0.5,
                "ALT": targets.get("ALT", 0.2) * 0.3,
                "STABLE": targets.get("STABLE", 0.3) * 1.5
            }
        elif risk_profile == "Aggressive":
            # Increase crypto, reduce stable
            return {
                "BTC": targets.get("BTC", 0.3) * 1.3,
                "ETH": targets.get("ETH", 0.2) * 1.3,
                "ALT": targets.get("ALT", 0.2) * 1.5,
                "STABLE": targets.get("STABLE", 0.3) * 0.7
            }
        else:  # Balanced (default)
            return targets
    
    def calculate_required_trades(
        self,
        current_positions: Dict,
        target_allocation: Dict,
        total_value: float
    ) -> Dict:
        """
        Calculate required trades to reach target allocation
        
        Args:
            current_positions: Current holdings {symbol: amount}
            target_allocation: Target amounts {symbol: amount}
            total_value: Total portfolio value
        
        Returns:
            Trades needed {symbol: (action, quantity, price)}
        """
        trades = {}
        
        for symbol, target_amount in target_allocation.items():
            current_amount = current_positions.get(symbol, 0)
            
            if current_amount != target_amount:
                # Get current price (simplified)
                price = current_amount / (current_positions.get(symbol + "_qty", 1) or 1)
                
                if target_amount > current_amount:
                    action = "BUY"
                    quantity = (target_amount - current_amount) / price
                else:
                    action = "SELL"
                    quantity = (current_amount - target_amount) / price
                
                trades[symbol] = {
                    "action": action,
                    "quantity": quantity,
                    "target_amount": target_amount
                }
        
        logger.info(f"Trades calculated: {len(trades)} positions")
        return trades
    
    def get_allocation_drift(
        self,
        current_positions: Dict,
        target_allocation: Dict,
        total_value: float
    ) -> Dict:
        """
        Calculate allocation drift from target
        
        Returns:
            Drift percentages for each asset
        """
        drift = {}
        
        for symbol, target_pct in target_allocation.items():
            current_amount = current_positions.get(symbol, 0)
            current_pct = current_amount / total_value if total_value > 0 else 0
            
            drift[symbol] = {
                "current_pct": current_pct,
                "target_pct": target_pct,
                "drift": abs(current_pct - target_pct)
            }
        
        return drift
