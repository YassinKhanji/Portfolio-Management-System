"""
Multi-Asset Trading System Architecture

This module provides a unified interface for managing multiple asset classes
(Crypto via Kraken, Traditional Assets via WealthSimple) through SnapTrade.

Key Components:
- RegimeDetector: Base class for market regime detection (Crypto, Traditional)
- AssetClassManager: Individual asset class managers
- MultiAssetPortfolioManager: Orchestrates allocation and execution across all assets
- UnifiedExecutor: Executes trades through SnapTrade API

Architecture:
    Market Data
        ↓
    [Crypto Regime]   [Traditional Regime]
        ↓                     ↓
    [Asset Class Managers]
        ↓
    [Multi-Asset Portfolio Manager]
        ↓
    [Allocation Strategy]
        ↓
    [Unified SnapTrade Executor]
        ↓
    [Kraken Account] / [WealthSimple Account]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class AssetClass(Enum):
    """Asset class enumeration"""
    CRYPTO = "crypto"           # Bitcoin, Ethereum, etc. on Kraken
    EQUITIES = "equities"       # Stocks on WealthSimple
    FIXED_INCOME = "fixed_income"  # Bonds on WealthSimple
    COMMODITIES = "commodities"    # Gold, etc. on WealthSimple


class MarketRegime(Enum):
    """Market regime classification"""
    RISK_OFF = "risk_off"
    RISK_ON = "risk_on"
    BALANCED = "balanced"
    SIDEWAYS = "sideways"


@dataclass
class RegimeState:
    """Current regime state for an asset class"""
    asset_class: AssetClass
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    vol_regime: int    # 0=tight, 1=normal, 2=volatile
    dir_regime: int    # 0=bearish, 1=sideways, 2=bullish
    timestamp: str


@dataclass
class Position:
    """Individual position in an account"""
    symbol: str
    asset_class: AssetClass
    quantity: float
    price: float
    value: float
    account_id: str  # Kraken or WealthSimple account ID
    
    @property
    def market_value(self) -> float:
        return self.quantity * self.price


@dataclass
class PortfolioAllocation:
    """Target allocation for asset class"""
    asset_class: AssetClass
    target_weight: float  # e.g., 0.40 for 40%
    symbols: List[str]    # e.g., ['BTC', 'ETH'] for crypto
    allocation: Dict[str, float]  # e.g., {'BTC': 0.25, 'ETH': 0.15}
    rebalance_threshold: float = 0.05  # Rebalance if drift > 5%


@dataclass
class RebalanceAction:
    """Single trade action"""
    account_id: str
    symbol: str
    asset_class: AssetClass
    side: str  # 'BUY' or 'SELL'
    quantity: float
    reason: str


# =============================================================================
# ABSTRACT BASE CLASSES
# =============================================================================

class RegimeDetector(ABC):
    """
    Base class for market regime detection.
    
    Subclasses must implement regime detection for specific asset classes.
    """
    
    def __init__(self, asset_class: AssetClass, lookback_days: int = 365):
        self.asset_class = asset_class
        self.lookback_days = lookback_days
        self.current_regime: Optional[RegimeState] = None
        
    @abstractmethod
    def fetch_data(self) -> bool:
        """Fetch market data. Return True if successful."""
        pass
    
    @abstractmethod
    def calculate_features(self) -> bool:
        """Calculate volatility and directional indicators."""
        pass
    
    @abstractmethod
    def detect_regime(self) -> RegimeState:
        """Detect current market regime."""
        pass
    
    def run_detection(self) -> RegimeState:
        """Execute full detection pipeline"""
        if not self.fetch_data():
            logger.error(f"Failed to fetch data for {self.asset_class.value}")
            return None
        
        if not self.calculate_features():
            logger.error(f"Failed to calculate features for {self.asset_class.value}")
            return None
        
        self.current_regime = self.detect_regime()
        logger.info(f"{self.asset_class.value} regime: {self.current_regime.regime.value}")
        return self.current_regime


class AllocationStrategy(ABC):
    """
    Base class for multi-asset allocation strategies.
    
    Determines target weights for each asset class based on regime.
    """
    
    def __init__(self, regimes: Dict[AssetClass, RegimeState]):
        self.regimes = regimes
        self.allocations: Dict[AssetClass, PortfolioAllocation] = {}
        
    @abstractmethod
    def calculate_weights(self) -> Dict[AssetClass, float]:
        """
        Calculate asset class weights based on regimes.
        
        Returns:
            Dict mapping AssetClass to weight (sum to 1.0)
        """
        pass
    
    @abstractmethod
    def get_symbols_per_class(self, asset_class: AssetClass) -> Dict[str, float]:
        """
        Determine symbol allocation within an asset class.
        
        Returns:
            Dict mapping symbol to weight within the asset class
        """
        pass
    
    def run_allocation(self) -> Dict[AssetClass, PortfolioAllocation]:
        """Execute allocation strategy"""
        weights = self.calculate_weights()
        
        for asset_class, weight in weights.items():
            symbols_alloc = self.get_symbols_per_class(asset_class)
            
            self.allocations[asset_class] = PortfolioAllocation(
                asset_class=asset_class,
                target_weight=weight,
                symbols=list(symbols_alloc.keys()),
                allocation=symbols_alloc
            )
        
        logger.info(f"Allocation calculated: {weights}")
        return self.allocations


class Executor(ABC):
    """
    Base class for order execution.
    
    Subclasses implement execution through specific brokers (SnapTrade).
    """
    
    @abstractmethod
    def execute_order(self, action: RebalanceAction) -> bool:
        """Execute single order. Return True if successful."""
        pass
    
    @abstractmethod
    def get_current_positions(self) -> Dict[str, Position]:
        """Get all current positions across all accounts."""
        pass


# =============================================================================
# EXAMPLE IMPLEMENTATIONS (STUBS)
# =============================================================================

class CryptoRegimeDetector(RegimeDetector):
    """Crypto regime detection using CCXT/Kraken data"""
    
    def __init__(self):
        super().__init__(AssetClass.CRYPTO, lookback_days=365*15)
        self.crypto_detector = None  # Will use regime_detection.CryptoRegimeDetector
    
    def fetch_data(self) -> bool:
        # Uses CryptoRegimeDetector from regime_detection.py
        # TODO: Integrate with regime_detection.py (CryptoRegimeDetector)
        logger.info("Fetching crypto data via CCXT/Kraken")
        return True
    
    def calculate_features(self) -> bool:
        # Volatility, directional indicators
        logger.info("Calculating crypto features")
        return True
    
    def detect_regime(self) -> RegimeState:
        # Maps regime_detection.py results to RegimeState
        logger.info("Detecting crypto regime")
        return RegimeState(
            asset_class=AssetClass.CRYPTO,
            regime=MarketRegime.RISK_ON,
            confidence=0.85,
            vol_regime=2,
            dir_regime=2,
            timestamp="2024-01-15T10:00:00Z"
        )


class TraditionalAssetsRegimeDetector(RegimeDetector):
    """Traditional assets regime detection (stocks, bonds via WealthSimple)"""
    
    def __init__(self):
        super().__init__(AssetClass.EQUITIES, lookback_days=365*5)
    
    def fetch_data(self) -> bool:
        # Fetch equity, bond indices via SnapTrade
        logger.info("Fetching traditional assets data")
        return True
    
    def calculate_features(self) -> bool:
        # Bond yields, volatility indices, correlation
        logger.info("Calculating traditional assets features")
        return True
    
    def detect_regime(self) -> RegimeState:
        # Detect bull/bear market, credit conditions
        logger.info("Detecting traditional assets regime")
        return RegimeState(
            asset_class=AssetClass.EQUITIES,
            regime=MarketRegime.BALANCED,
            confidence=0.75,
            vol_regime=1,
            dir_regime=1,
            timestamp="2024-01-15T10:00:00Z"
        )


class MultiAssetAllocationStrategy(AllocationStrategy):
    """Sample allocation strategy across crypto and traditional assets"""
    
    def calculate_weights(self) -> Dict[AssetClass, float]:
        """
        Example: Allocate based on regime conditions
        
        Risk Off:       60% Traditional / 40% Crypto → 30% Equity / 15% Bonds / 40% Crypto
        Balanced:       50% Traditional / 50% Crypto → 30% Equity / 20% Bonds / 50% Crypto
        Risk On:        30% Traditional / 70% Crypto → 15% Equity / 15% Bonds / 70% Crypto
        """
        crypto_regime = self.regimes.get(AssetClass.CRYPTO)
        equity_regime = self.regimes.get(AssetClass.EQUITIES)
        
        if crypto_regime.regime == MarketRegime.RISK_OFF:
            return {
                AssetClass.CRYPTO: 0.40,
                AssetClass.EQUITIES: 0.30,
                AssetClass.FIXED_INCOME: 0.30
            }
        elif crypto_regime.regime == MarketRegime.RISK_ON:
            return {
                AssetClass.CRYPTO: 0.70,
                AssetClass.EQUITIES: 0.20,
                AssetClass.FIXED_INCOME: 0.10
            }
        else:
            return {
                AssetClass.CRYPTO: 0.50,
                AssetClass.EQUITIES: 0.30,
                AssetClass.FIXED_INCOME: 0.20
            }
    
    def get_symbols_per_class(self, asset_class: AssetClass) -> Dict[str, float]:
        """Within-asset-class allocation"""
        if asset_class == AssetClass.CRYPTO:
            # Crypto: BTC heavy, ETH secondary, alts balanced
            return {
                'BTC': 0.60,
                'ETH': 0.25,
                'ALT': 0.15
            }
        elif asset_class == AssetClass.EQUITIES:
            # Equities: Diversified across sectors
            return {
                'SPY': 0.40,   # US Large Cap
                'VEA': 0.35,   # Developed International
                'VWO': 0.25    # Emerging Markets
            }
        elif asset_class == AssetClass.FIXED_INCOME:
            # Bonds: Mix of government and corporate
            return {
                'BND': 0.60,   # Total Bond Market
                'VGIT': 0.40   # Intermediate Govt Bonds
            }
        else:
            return {}


# =============================================================================
# ORCHESTRATION
# =============================================================================

class MultiAssetPortfolioManager:
    """
    Master orchestrator for the entire portfolio.
    
    Manages regime detection, allocation, and execution across multiple asset classes.
    """
    
    def __init__(self, snaptrade_client):
        """
        Initialize portfolio manager.
        
        Args:
            snaptrade_client: SnapTrade API client for execution
        """
        self.snaptrade_client = snaptrade_client
        
        # Initialize regime detectors
        self.regime_detectors: Dict[AssetClass, RegimeDetector] = {
            AssetClass.CRYPTO: CryptoRegimeDetector(),
            AssetClass.EQUITIES: TraditionalAssetsRegimeDetector(),
        }
        
        self.current_regimes: Dict[AssetClass, RegimeState] = {}
        self.current_allocations: Dict[AssetClass, PortfolioAllocation] = {}
        self.current_positions: Dict[str, Position] = {}
        
        logger.info("MultiAssetPortfolioManager initialized")
    
    def detect_regimes(self) -> Dict[AssetClass, RegimeState]:
        """
        Run regime detection for all asset classes in parallel (if possible).
        
        Returns:
            Dict mapping AssetClass to RegimeState
        """
        logger.info("Starting regime detection across all asset classes...")
        
        for asset_class, detector in self.regime_detectors.items():
            self.current_regimes[asset_class] = detector.run_detection()
        
        logger.info(f"Regime detection complete: {self.current_regimes}")
        return self.current_regimes
    
    def calculate_allocations(self) -> Dict[AssetClass, PortfolioAllocation]:
        """
        Calculate target allocations based on detected regimes.
        
        Returns:
            Dict mapping AssetClass to PortfolioAllocation
        """
        logger.info("Calculating allocations...")
        
        strategy = MultiAssetAllocationStrategy(self.current_regimes)
        self.current_allocations = strategy.run_allocation()
        
        return self.current_allocations
    
    def get_rebalance_actions(self) -> List[RebalanceAction]:
        """
        Determine which trades are needed to reach target allocation.
        
        Returns:
            List of RebalanceAction objects
        """
        logger.info("Calculating rebalance actions...")
        
        actions: List[RebalanceAction] = []
        
        # Get current portfolio value
        total_value = sum(pos.value for pos in self.current_positions.values())
        
        # For each asset class
        for asset_class, target_alloc in self.current_allocations.items():
            target_value = total_value * target_alloc.target_weight
            
            # Get current value for this asset class
            current_value = sum(
                pos.value for pos in self.current_positions.values()
                if pos.asset_class == asset_class
            )
            
            drift = abs(target_value - current_value) / total_value
            
            # Only rebalance if drift exceeds threshold
            if drift > target_alloc.rebalance_threshold:
                logger.info(
                    f"{asset_class.value}: drift={drift:.2%}, rebalancing..."
                )
                
                # Generate individual trades for each symbol
                for symbol, weight_in_class in target_alloc.allocation.items():
                    target_symbol_value = target_value * weight_in_class
                    
                    # TODO: Find current position for this symbol
                    # TODO: Calculate delta
                    # TODO: Create RebalanceAction
                    pass
        
        logger.info(f"Generated {len(actions)} rebalance actions")
        return actions
    
    def execute_rebalancing(self, actions: List[RebalanceAction]) -> bool:
        """
        Execute all rebalancing trades.
        
        Args:
            actions: List of RebalanceAction objects
            
        Returns:
            True if all trades executed successfully
        """
        logger.info(f"Executing {len(actions)} trades...")
        
        success_count = 0
        
        for action in actions:
            try:
                # Execute through SnapTrade client
                # This abstracts away Kraken vs WealthSimple differences
                result = self.snaptrade_client.execute_trade(
                    account_id=action.account_id,
                    symbol=action.symbol,
                    side=action.side,
                    quantity=action.quantity
                )
                
                if result.success:
                    success_count += 1
                    logger.info(
                        f"✓ {action.side} {action.quantity} {action.symbol} "
                        f"({action.asset_class.value})"
                    )
                else:
                    logger.warning(f"✗ Failed to {action.side} {action.symbol}")
            
            except Exception as e:
                logger.error(f"Error executing {action.symbol}: {e}")
        
        logger.info(f"Rebalancing complete: {success_count}/{len(actions)} trades")
        return success_count == len(actions)
    
    def rebalance(self) -> bool:
        """
        Execute full rebalancing cycle:
        1. Detect regimes
        2. Calculate allocations
        3. Generate actions
        4. Execute trades
        
        Returns:
            True if rebalancing completed successfully
        """
        logger.info("=" * 70)
        logger.info("REBALANCING CYCLE STARTED")
        logger.info("=" * 70)
        
        try:
            # Step 1: Detect regimes
            self.detect_regimes()
            
            # Step 2: Calculate allocations
            self.calculate_allocations()
            
            # Step 3: Generate rebalance actions
            actions = self.get_rebalance_actions()
            
            if not actions:
                logger.info("No rebalancing needed")
                return True
            
            # Step 4: Execute trades
            success = self.execute_rebalancing(actions)
            
            logger.info("=" * 70)
            logger.info("REBALANCING CYCLE COMPLETE")
            logger.info("=" * 70)
            
            return success
        
        except Exception as e:
            logger.error(f"Rebalancing failed: {e}", exc_info=True)
            return False


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

"""
Example usage in your FastAPI endpoint:

from app.services.snaptrade_integration import SnapTradeClient

@app.post("/api/rebalance/multi-asset")
async def rebalance_multi_asset(user_id: str):
    '''Rebalance portfolio across crypto (Kraken) and traditional (WealthSimple)'''
    
    # Initialize SnapTrade client
    snaptrade_client = SnapTradeClient(user_token=user_snaptrade_token)
    
    # Create portfolio manager
    portfolio_manager = MultiAssetPortfolioManager(snaptrade_client)
    
    # Execute rebalancing
    success = portfolio_manager.rebalance()
    
    return {
        "status": "success" if success else "failed",
        "regimes": portfolio_manager.current_regimes,
        "allocations": portfolio_manager.current_allocations
    }
"""
