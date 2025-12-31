"""
Multi-Asset Portfolio Manager

Orchestrates portfolio management across multiple asset classes:
- Cryptocurrency (via Kraken through SnapTrade)
- Traditional Assets (Equities, Fixed Income via WealthSimple through SnapTrade)

Responsibilities:
1. Aggregates regime signals from both asset classes
2. Computes portfolio-level allocations
3. Rebalances across assets
4. Manages constraints and risk limits
5. Executes trades through unified SnapTrade integration
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class RegimeSignal:
    """Unified regime signal from either asset class"""
    asset_class: str  # "CRYPTO" or "TRADITIONAL"
    regime: str  # e.g., "BULL", "BEAR", "CONSOLIDATION"
    confidence: float  # 0.0 to 1.0
    signal_timestamp: datetime
    features: Dict[str, float] = field(default_factory=dict)
    
    def is_strong(self) -> bool:
        """Returns True if confidence > 0.70"""
        return self.confidence > 0.70


@dataclass
class AssetAllocation:
    """Allocation for a single asset"""
    ticker: str
    asset_class: str  # "CRYPTO" or "TRADITIONAL"
    target_weight: float  # 0.0 to 1.0
    current_weight: float  # Current portfolio weight
    target_value: float  # Dollar amount
    rebalance_needed: bool
    reason: str  # Why this allocation changed


@dataclass
class PortfolioAllocation:
    """Complete portfolio allocation across all assets"""
    timestamp: datetime
    total_value: float
    regime_profile: str  # e.g., "BULL", "BEAR", "BALANCED"
    
    # Asset class allocations
    crypto_allocation: float  # % of portfolio
    traditional_allocation: float  # % of portfolio
    cash_allocation: float  # % of portfolio
    
    # Individual asset allocations
    allocations: List[AssetAllocation] = field(default_factory=list)
    
    # Metrics
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float


# =============================================================================
# MULTI-ASSET PORTFOLIO MANAGER
# =============================================================================

class MultiAssetPortfolioManager:
    """
    Manages portfolio allocation across crypto and traditional assets.
    
    Architecture:
    - Receives regime signals from both crypto and traditional regime detectors
    - Computes optimal allocation using a tiered decision framework
    - Rebalances when allocations drift beyond thresholds
    - Executes through unified SnapTrade executor
    """
    
    def __init__(self, initial_portfolio_value: float = 100_000):
        """
        Initialize multi-asset portfolio manager.
        
        Args:
            initial_portfolio_value: Starting portfolio value
        """
        self.initial_value = initial_portfolio_value
        self.current_value = initial_portfolio_value
        
        # Regime signals
        self.crypto_regime: Optional[RegimeSignal] = None
        self.traditional_regime: Optional[RegimeSignal] = None
        
        # Current allocations
        self.portfolio_allocation: Optional[PortfolioAllocation] = None
        self.asset_positions: Dict[str, float] = {}  # ticker -> value
        
        # Configuration
        self.rebalance_threshold = 0.05  # Rebalance if drift > 5%
        self.rebalance_frequency_days = 30  # Minimum days between rebalances
        self.last_rebalance_date: Optional[datetime] = None
        
        # Risk limits
        self.max_crypto_allocation = 0.30  # Max 30% in crypto
        self.min_cash_allocation = 0.05  # Min 5% cash
        
        logger.info("Initialized MultiAssetPortfolioManager")
    
    # =========================================================================
    # REGIME SIGNAL INTEGRATION
    # =========================================================================
    
    def update_crypto_regime(self, regime_signal: RegimeSignal) -> None:
        """Update regime signal from crypto detector"""
        logger.info("Updated crypto regime")
        self.crypto_regime = regime_signal
    
    def update_traditional_regime(self, regime_signal: RegimeSignal) -> None:
        """Update regime signal from traditional assets detector"""
        logger.info("Updated traditional regime")
        self.traditional_regime = regime_signal
    
    def get_aggregate_regime(self) -> str:
        """
        Compute aggregate market regime from both asset classes.
        
        Logic:
        - If both are BULL: Aggressive
        - If both are BEAR: Defensive
        - If conflicting: Wait for clarity
        - Crypto and traditional can have uncorrelated regimes
        """
        if not self.crypto_regime or not self.traditional_regime:
            return "PENDING"  # Waiting for both signals
        
        crypto = self.crypto_regime.regime
        traditional = self.traditional_regime.regime
        
        # Both bullish
        if crypto in ["BULL", "UPTREND"] and traditional in ["BULL"]:
            return "AGGRESSIVE"
        
        # Both bearish
        if crypto in ["BEAR", "DOWNTREND"] and traditional in ["BEAR", "CORRECTION"]:
            return "DEFENSIVE"
        
        # Crypto bull, traditional defensive
        if crypto in ["BULL", "UPTREND"] and traditional in ["BEAR", "CORRECTION"]:
            return "BALANCED"  # Partial offset
        
        # Crypto bear, traditional bull
        if crypto in ["BEAR", "DOWNTREND"] and traditional in ["BULL"]:
            return "TRADITIONAL_FOCUSED"
        
        # Default to balanced
        return "BALANCED"
    
    # =========================================================================
    # ALLOCATION COMPUTATION
    # =========================================================================
    
    def compute_allocation(self) -> Optional[PortfolioAllocation]:
        """
        Compute optimal portfolio allocation based on regime signals.
        
        Allocation Logic:
        1. Start with base allocation for current regime
        2. Adjust for confidence levels
        3. Apply constraints (max crypto, min cash)
        4. Distribute within each asset class
        
        Returns:
            PortfolioAllocation with target weights and individual asset allocations
        """
        
        if not self.crypto_regime or not self.traditional_regime:
            logger.warning("Cannot compute allocation: Missing regime signals")
            return None
        
        aggregate_regime = self.get_aggregate_regime()
        logger.info("Computing allocation")
        
        # Get base allocation for aggregate regime
        base_allocation = self._get_base_allocation(aggregate_regime)
        
        # Adjust based on signal confidence
        crypto_weight = self._adjust_for_confidence(
            base_allocation["crypto"],
            self.crypto_regime.confidence
        )
        traditional_weight = self._adjust_for_confidence(
            base_allocation["traditional"],
            self.traditional_regime.confidence
        )
        cash_weight = 1.0 - crypto_weight - traditional_weight
        
        # Apply constraints
        crypto_weight = min(crypto_weight, self.max_crypto_allocation)
        cash_weight = max(cash_weight, self.min_cash_allocation)
        traditional_weight = 1.0 - crypto_weight - cash_weight
        
        logger.info("Target allocation computed")
        
        # Distribute within crypto
        crypto_allocations = self._allocate_crypto_assets(
            crypto_weight,
            self.crypto_regime
        )
        
        # Distribute within traditional assets
        traditional_allocations = self._allocate_traditional_assets(
            traditional_weight,
            self.traditional_regime
        )
        
        # Combine all allocations
        all_allocations = crypto_allocations + traditional_allocations
        
        # Compute portfolio metrics
        portfolio = PortfolioAllocation(
            timestamp=datetime.now(),
            total_value=self.current_value,
            regime_profile=aggregate_regime,
            crypto_allocation=crypto_weight,
            traditional_allocation=traditional_weight,
            cash_allocation=cash_weight,
            allocations=all_allocations,
            expected_return=self._compute_expected_return(all_allocations),
            expected_volatility=self._compute_volatility(all_allocations),
            sharpe_ratio=0.0  # TODO: Compute Sharpe ratio
        )
        
        self.portfolio_allocation = portfolio
        
        return portfolio
    
    # =========================================================================
    # ASSET CLASS ALLOCATION
    # =========================================================================
    
    def _allocate_crypto_assets(
        self,
        total_weight: float,
        regime: RegimeSignal
    ) -> List[AssetAllocation]:
        """Allocate crypto portion of portfolio"""
        
        allocations = []
        
        # Crypto allocation logic depends on regime
        if regime.regime in ["BULL", "UPTREND"]:
            # Bull market: More diversified, include smaller caps
            allocation_dict = {
                "BTC": 0.50,   # Bitcoin core
                "ETH": 0.30,   # Ethereum
                "ALT": 0.20,   # Altcoins (SOL, AVAX, etc.)
            }
        elif regime.regime in ["BEAR", "DOWNTREND"]:
            # Bear market: Concentrate in blue chips
            allocation_dict = {
                "BTC": 0.70,
                "ETH": 0.25,
                "ALT": 0.05,
            }
        else:
            # Consolidation: Balanced
            allocation_dict = {
                "BTC": 0.60,
                "ETH": 0.30,
                "ALT": 0.10,
            }
        
        for crypto, allocation_pct in allocation_dict.items():
            target_weight = total_weight * allocation_pct
            target_value = self.current_value * target_weight
            current_value = self.asset_positions.get(crypto, 0)
            current_weight = current_value / self.current_value if self.current_value > 0 else 0
            
            allocations.append(AssetAllocation(
                ticker=crypto,
                asset_class="CRYPTO",
                target_weight=target_weight,
                current_weight=current_weight,
                target_value=target_value,
                rebalance_needed=abs(target_weight - current_weight) > self.rebalance_threshold,
                reason=f"Crypto regime: {regime.regime}"
            ))
        
        return allocations
    
    def _allocate_traditional_assets(
        self,
        total_weight: float,
        regime: RegimeSignal
    ) -> List[AssetAllocation]:
        """Allocate traditional assets (equities + fixed income) portion of portfolio"""
        
        allocations = []
        
        # Traditional asset allocation logic
        if regime.regime == "BULL":
            # Bull market: Favor equities, especially growth
            allocation_dict = {
                "SPY": 0.40,   # US Large Cap
                "QQQ": 0.15,   # Tech/Growth
                "IWM": 0.10,   # US Small Cap
                "VEA": 0.08,   # International
                "BND": 0.20,   # Bonds (diversification)
                "TLT": 0.07,   # Long-term Treasuries (tail risk)
            }
        elif regime.regime == "BEAR":
            # Bear market: Defensive, favor bonds and dividend stocks
            allocation_dict = {
                "SPY": 0.15,   # US Large Cap (reduced)
                "QQQ": 0.05,   # Tech (minimal)
                "IWM": 0.00,   # Small Cap (avoid)
                "VEA": 0.00,   # International (avoid)
                "BND": 0.45,   # Total Bond Market
                "TLT": 0.35,   # Long-term Treasuries (flight to quality)
            }
        elif regime.regime == "CORRECTION":
            # Pullback: Slightly defensive, maintain quality
            allocation_dict = {
                "SPY": 0.25,
                "QQQ": 0.10,
                "IWM": 0.05,
                "VEA": 0.05,
                "BND": 0.35,
                "TLT": 0.20,
            }
        elif regime.regime == "FLIGHT_TO_QUALITY":
            # Credit stress: Favor government bonds
            allocation_dict = {
                "SPY": 0.10,
                "QQQ": 0.00,
                "IWM": 0.00,
                "VEA": 0.00,
                "BND": 0.30,
                "TLT": 0.60,   # Maximize safety
            }
        else:  # CONSOLIDATION, DIVIDEND_SEASON, etc.
            # Balanced approach
            allocation_dict = {
                "SPY": 0.30,
                "QQQ": 0.12,
                "IWM": 0.08,
                "VEA": 0.05,
                "BND": 0.30,
                "TLT": 0.15,
            }
        
        for ticker, allocation_pct in allocation_dict.items():
            target_weight = total_weight * allocation_pct
            target_value = self.current_value * target_weight
            current_value = self.asset_positions.get(ticker, 0)
            current_weight = current_value / self.current_value if self.current_value > 0 else 0
            
            allocations.append(AssetAllocation(
                ticker=ticker,
                asset_class="TRADITIONAL",
                target_weight=target_weight,
                current_weight=current_weight,
                target_value=target_value,
                rebalance_needed=abs(target_weight - current_weight) > self.rebalance_threshold,
                reason=f"Traditional regime: {regime.regime}"
            ))
        
        return allocations
    
    # =========================================================================
    # REBALANCING
    # =========================================================================
    
    def should_rebalance(self) -> bool:
        """
        Determine if portfolio needs rebalancing.
        
        Triggers:
        1. Any allocation drifts > threshold
        2. Minimum time elapsed since last rebalance
        3. Major regime change
        
        Returns:
            True if rebalancing needed
        """
        if not self.portfolio_allocation:
            return False
        
        # Check time since last rebalance
        if self.last_rebalance_date:
            days_since = (datetime.now() - self.last_rebalance_date).days
            if days_since < self.rebalance_frequency_days:
                logger.debug("Skipping rebalance: too soon since last rebalance")
                return False
        
        # Check if any allocation drifted beyond threshold
        for allocation in self.portfolio_allocation.allocations:
            if allocation.rebalance_needed:
                logger.info("Rebalancing needed")
                return True
        
        return False
    
    def get_rebalance_trades(self) -> List[Dict]:
        """
        Get list of trades needed to rebalance portfolio.
        
        Returns:
            List of trade specifications (ticker, direction, quantity)
        """
        if not self.portfolio_allocation:
            return []
        
        trades = []
        
        for allocation in self.portfolio_allocation.allocations:
            drift = allocation.target_weight - allocation.current_weight
            
            if abs(drift) > self.rebalance_threshold:
                trade_value = drift * self.current_value
                
                trades.append({
                    'ticker': allocation.ticker,
                    'asset_class': allocation.asset_class,
                    'direction': 'BUY' if trade_value > 0 else 'SELL',
                    'value': abs(trade_value),
                    'reason': allocation.reason
                })
        
        return trades
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _get_base_allocation(self, regime: str) -> Dict[str, float]:
        """Get base allocation for a given regime"""
        
        allocations = {
            "AGGRESSIVE": {
                "crypto": 0.25,
                "traditional": 0.70,
            },
            "DEFENSIVE": {
                "crypto": 0.10,
                "traditional": 0.55,
            },
            "BALANCED": {
                "crypto": 0.15,
                "traditional": 0.65,
            },
            "TRADITIONAL_FOCUSED": {
                "crypto": 0.05,
                "traditional": 0.75,
            },
            "PENDING": {
                "crypto": 0.15,
                "traditional": 0.65,
            },
        }
        
        return allocations.get(regime, allocations["BALANCED"])
    
    def _adjust_for_confidence(self, base_weight: float, confidence: float) -> float:
        """
        Adjust allocation weight based on signal confidence.
        
        High confidence (>0.7): Maintain allocation
        Medium confidence (0.5-0.7): Reduce slightly
        Low confidence (<0.5): Reduce significantly
        """
        if confidence > 0.70:
            return base_weight
        elif confidence > 0.50:
            return base_weight * 0.85
        else:
            return base_weight * 0.70
    
    def _compute_expected_return(self, allocations: List[AssetAllocation]) -> float:
        """Compute portfolio expected return"""
        # TODO: Implement based on asset returns
        return 0.08  # Placeholder
    
    def _compute_volatility(self, allocations: List[AssetAllocation]) -> float:
        """Compute portfolio volatility"""
        # TODO: Implement based on asset volatilities and correlations
        return 0.12  # Placeholder
    
    # =========================================================================
    # PORTFOLIO UPDATES
    # =========================================================================
    
    def update_position(self, ticker: str, value: float) -> None:
        """Update current position value for an asset"""
        self.asset_positions[ticker] = value
        self.current_value = sum(self.asset_positions.values())
        logger.debug("Updated position value")
    
    def record_rebalance(self) -> None:
        """Record that a rebalance was performed"""
        self.last_rebalance_date = datetime.now()
        logger.info("Rebalance recorded")


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    # Example workflow
    manager = MultiAssetPortfolioManager(initial_portfolio_value=500_000)
    
    # Receive regime signals
    crypto_signal = RegimeSignal(
        asset_class="CRYPTO",
        regime="BULL",
        confidence=0.85,
        signal_timestamp=datetime.now(),
        features={"momentum": 0.15, "rsi": 65}
    )
    
    traditional_signal = RegimeSignal(
        asset_class="TRADITIONAL",
        regime="CONSOLIDATION",
        confidence=0.60,
        signal_timestamp=datetime.now(),
        features={"spy_momentum": 0.02, "vix": 18}
    )
    
    manager.update_crypto_regime(crypto_signal)
    manager.update_traditional_regime(traditional_signal)
    
    # Compute allocation
    allocation = manager.compute_allocation()
    
    if allocation:
        print(f"\nAggregate Regime: {allocation.regime_profile}")
        print(f"Portfolio Value: ${allocation.total_value:,.2f}")
        print(f"\nTarget Allocations:")
        print(f"  Crypto: {allocation.crypto_allocation:.1%}")
        print(f"  Traditional: {allocation.traditional_allocation:.1%}")
        print(f"  Cash: {allocation.cash_allocation:.1%}")
        
        print(f"\nIndividual Assets:")
        for alloc in allocation.allocations:
            print(f"  {alloc.ticker}: {alloc.target_weight:.1%} (${alloc.target_value:,.0f})")
