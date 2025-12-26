"""
Traditional Assets Regime Detection

EQUITIES & BONDS ONLY - Uses yfinance for traditional market data.
Detects market regimes for equities, bonds, and commodities.
Signals: Bull Market, Bear Market, Risk-Off, Dividend Season, etc.

Data Source: yfinance (Yahoo Finance)

This is COMPLETELY SEPARATE from Crypto Regime Detection:
- Crypto Analysis: Uses regime_detection.py + CCXT/Kraken data → Different strategy & allocation
- Equities Analysis: Uses this module + yfinance data → Different strategy & allocation
- Execution: BOTH systems executed by SnapTrade API

Different strategies and indicators than crypto system.
Uses macroeconomic indicators (traditional markets, business days only):
- Stock market momentum (S&P 500, Russell 2000, QQQ)
- Bond yields and credit spreads
- USD strength
- Volatility indices (VIX)
- Slower timeframes (months vs crypto's days/weeks)
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional, Dict, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TraditionalAssetsRegimeDetector:
    """
    Detects market regime for traditional assets (equities and fixed income).
    
    Data Source: yfinance (Yahoo Finance)
    Strategies: Macro-based (fundamentals, yields, business cycles)
    Execution: SnapTrade API (trades executed on WealthSimple account)
    
    Market Regimes:
    - BULL: Risk-on, strong equity momentum, falling yields
    - BEAR: Risk-off, weak equities, rising yields, flight to quality
    - CORRECTION: Temporary pullback in bull market
    - CONSOLIDATION: Low volatility, sideways movement
    - DIVIDEND_SEASON: High dividend yields, income focus
    - FLIGHT_TO_QUALITY: Bonds outperforming stocks
    
    COMPLETELY SEPARATE from crypto system - different analysis, different allocation strategy.
    """
    
    def __init__(self, lookback_days: int = 1825):  # 5 years
        """
        Initialize regime detector for traditional assets.
        
        Args:
            lookback_days: Historical lookback period (default 5 years)
        """
        self.lookback_days = lookback_days
        self.lookback_start = datetime.now() - timedelta(days=lookback_days)
        
        # Key tickers for regime detection
        self.equity_tickers = {
            'SPY': 'US Large Cap',
            'IWM': 'US Small Cap', 
            'QQQ': 'US Tech/Growth',
            'EEM': 'Emerging Markets',
            'VEA': 'Developed Markets',
        }
        
        self.fixed_income_tickers = {
            'TLT': 'Long-term Treasuries',
            'IEF': 'Intermediate Treasuries',
            'SHY': 'Short-term Treasuries',
            'BND': 'Total Bond Market',
            'LQD': 'Investment Grade Corp Bonds',
            'HYG': 'High Yield Corp Bonds',
        }
        
        self.volatility_tickers = {
            'VIX': 'Equity Volatility',
            'MOVE': 'Bond Volatility',
        }
        
        self.commodity_tickers = {
            'GLD': 'Gold',
            'USO': 'Oil',
            'DXY': 'US Dollar Index',
        }
        
        # Storage for market data
        self.equity_data: Dict[str, pd.DataFrame] = {}
        self.fixed_income_data: Dict[str, pd.DataFrame] = {}
        self.volatility_data: Dict[str, pd.DataFrame] = {}
        self.commodity_data: Dict[str, pd.DataFrame] = {}
        
        # Calculated features
        self.features: Dict[str, float] = {}
        self.regime: Optional[str] = None
        self.confidence: float = 0.0
    
    def fetch_market_data(self) -> bool:
        """
        Fetch historical market data for regime detection.
        
        Data Source: yfinance (Yahoo Finance)
        
        Fetches data for:
        - Equity indices (SPY, QQQ, IWM, etc.)
        - Bond indices (TLT, IEF, BND, etc.)
        - Volatility indices (VIX, MOVE)
        - Commodities (GLD, USO, DXY)
        
        Returns:
            True if data successfully fetched
        """
        logger.info("Fetching traditional assets market data from yfinance...")
        
        try:
            import yfinance as yf
            
            # Fetch equity indices
            logger.info("Fetching equity indices...")
            for ticker, name in self.equity_tickers.items():
                try:
                    logger.debug(f"Fetching {ticker} ({name}) from yfinance...")
                    data = yf.download(
                        ticker, 
                        start=self.lookback_start, 
                        end=datetime.now(),
                        progress=False,
                        show_errors=False
                    )
                    if not data.empty:
                        self.equity_data[ticker] = data
                        logger.debug(f"Successfully fetched {len(data)} days for {ticker}")
                    else:
                        logger.warning(f"No data returned for {ticker}")
                except Exception as e:
                    logger.error(f"Failed to fetch {ticker}: {e}")
                    continue
            
            # Fetch bond indices
            logger.info("Fetching bond indices...")
            for ticker, name in self.fixed_income_tickers.items():
                try:
                    logger.debug(f"Fetching {ticker} ({name})...")
                    data = yf.download(
                        ticker, 
                        start=self.lookback_start, 
                        end=datetime.now(),
                        progress=False,
                        show_errors=False
                    )
                    if not data.empty:
                        self.fixed_income_data[ticker] = data
                        logger.debug(f"Successfully fetched {len(data)} days for {ticker}")
                except Exception as e:
                    logger.error(f"Failed to fetch {ticker}: {e}")
                    continue
            
            # Fetch volatility indices
            logger.info("Fetching volatility indices...")
            for ticker, name in self.volatility_tickers.items():
                try:
                    logger.debug(f"Fetching {ticker} ({name})...")
                    data = yf.download(
                        ticker, 
                        start=self.lookback_start, 
                        end=datetime.now(),
                        progress=False,
                        show_errors=False
                    )
                    if not data.empty:
                        self.volatility_data[ticker] = data
                        logger.debug(f"Successfully fetched {len(data)} days for {ticker}")
                except Exception as e:
                    logger.error(f"Failed to fetch {ticker}: {e}")
                    continue
            
            # Fetch commodities
            logger.info("Fetching commodity indices...")
            for ticker, name in self.commodity_tickers.items():
                try:
                    logger.debug(f"Fetching {ticker} ({name})...")
                    data = yf.download(
                        ticker, 
                        start=self.lookback_start, 
                        end=datetime.now(),
                        progress=False,
                        show_errors=False
                    )
                    if not data.empty:
                        self.commodity_data[ticker] = data
                        logger.debug(f"Successfully fetched {len(data)} days for {ticker}")
                except Exception as e:
                    logger.error(f"Failed to fetch {ticker}: {e}")
                    continue
            
            # Check if we have enough data
            total_datasets = (
                len(self.equity_data) + 
                len(self.fixed_income_data) + 
                len(self.volatility_data) + 
                len(self.commodity_data)
            )
            
            if total_datasets == 0:
                logger.error("No market data fetched at all")
                return False
            
            logger.info(f"Market data fetched successfully: {len(self.equity_data)} equities, "
                       f"{len(self.fixed_income_data)} bonds, {len(self.volatility_data)} volatility, "
                       f"{len(self.commodity_data)} commodities")
            return True
        
        except ImportError:
            logger.error("yfinance library not installed. Install with: pip install yfinance")
            return False
        except Exception as e:
            logger.error(f"Failed to fetch market data: {e}")
            return False
    
    def calculate_features(self) -> bool:
        """
        Calculate key market regime indicators.
        
        Features calculated:
        1. Equity Momentum: 3-month, 6-month, 12-month returns
        2. Bond Performance: Treasury yield curve, credit spreads
        3. Volatility: VIX level, volatility regime
        4. Correlation: Equity-Bond correlation
        5. Relative Strength: Tech vs Value, Growth vs Dividend
        6. Valuation: Price to earnings ratios
        7. Macroeconomic: GDP growth expectations, inflation
        
        Returns:
            True if features calculated successfully
        """
        logger.info("Calculating market regime features...")
        
        try:
            # === EQUITY MOMENTUM ===
            # 3-month momentum
            spy_1m = self._calculate_returns('SPY', 21)
            spy_3m = self._calculate_returns('SPY', 63)
            spy_6m = self._calculate_returns('SPY', 126)
            spy_12m = self._calculate_returns('SPY', 252)
            
            self.features['equity_momentum_1m'] = spy_1m
            self.features['equity_momentum_3m'] = spy_3m
            self.features['equity_momentum_6m'] = spy_6m
            self.features['equity_momentum_12m'] = spy_12m
            
            # Relative strength: Tech vs Value
            qqq_momentum = self._calculate_returns('QQQ', 126)
            iwm_momentum = self._calculate_returns('IWM', 126)
            self.features['growth_vs_value'] = qqq_momentum - iwm_momentum
            
            # === FIXED INCOME ===
            # Bond yield levels (inverted for regime: lower = better for equities)
            tlt_performance = self._calculate_returns('TLT', 126)
            ief_performance = self._calculate_returns('IEF', 126)
            
            self.features['long_bond_performance'] = tlt_performance
            self.features['mid_bond_performance'] = ief_performance
            
            # Treasury yield curve (normal, flat, or inverted)
            # NOTE: In real implementation, fetch actual yields
            self.features['yield_curve_slope'] = self._estimate_yield_curve()
            
            # Credit spreads
            lqd_spread = self._calculate_spread('LQD', 'IEF')
            hyg_spread = self._calculate_spread('HYG', 'IEF')
            
            self.features['ig_credit_spread'] = lqd_spread
            self.features['hy_credit_spread'] = hyg_spread
            
            # === VOLATILITY ===
            # VIX level regime
            vix_level = self._get_latest_level('VIX') if 'VIX' in self.volatility_data else 15
            self.features['vix_level'] = vix_level
            self.features['vix_regime'] = self._classify_vix(vix_level)
            
            # VIX trend
            vix_momentum = self._calculate_returns('VIX', 63)
            self.features['vix_momentum'] = vix_momentum
            
            # === CORRELATIONS ===
            # Equity-Bond correlation (divergence when risk-off)
            spy_bond_corr = self._calculate_correlation('SPY', 'BND', 126)
            self.features['spy_bond_correlation'] = spy_bond_corr
            
            # === COMMODITIES & INFLATION ===
            # Gold performance (inverse equity risk indicator)
            gld_momentum = self._calculate_returns('GLD', 126)
            self.features['gold_momentum'] = gld_momentum
            
            # USD strength (inverse commodity risk)
            dxy_momentum = self._calculate_returns('DXY', 126)
            self.features['usd_strength'] = dxy_momentum
            
            # === DIVIDEND METRICS ===
            # Dividend yield regime
            spy_div_yield = self._estimate_dividend_yield('SPY')
            self.features['dividend_yield'] = spy_div_yield
            
            logger.info("Features calculated successfully")
            logger.debug(f"Features: {self.features}")
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to calculate features: {e}")
            return False
    
    def detect_regime(self) -> tuple[str, float]:
        """
        Detect current market regime based on features.
        
        Returns:
            Tuple of (regime_name, confidence_score)
        """
        logger.info("Detecting market regime...")
        
        momentum = self.features.get('equity_momentum_6m', 0)
        vix = self.features.get('vix_level', 15)
        credit_spread = self.features.get('ig_credit_spread', 0)
        yield_curve = self.features.get('yield_curve_slope', 0.5)
        bond_perf = self.features.get('long_bond_performance', 0)
        spy_bond_corr = self.features.get('spy_bond_correlation', 0)
        
        # Decision tree for regime classification
        regime = "CONSOLIDATION"  # Default
        confidence = 0.5
        
        # BULL MARKET: Positive momentum, low VIX, normal/steep yield curve
        if momentum > 0.10 and vix < 20 and yield_curve > 0.5:
            regime = "BULL"
            confidence = min(0.95, 0.6 + momentum * 2 + (30 - vix) * 0.01)
        
        # BEAR MARKET: Negative momentum, high VIX, flight to bonds
        elif momentum < -0.05 and vix > 25:
            regime = "BEAR"
            confidence = min(0.95, 0.6 + abs(momentum) * 2 + (vix - 15) * 0.02)
        
        # CORRECTION: Moderate negative momentum but not full bear
        elif momentum < -0.02 and momentum > -0.05 and vix < 30:
            regime = "CORRECTION"
            confidence = 0.65
        
        # FLIGHT TO QUALITY: Bonds outperforming, credit spreads widening
        elif bond_perf > 0.05 and credit_spread > 0.02:
            regime = "FLIGHT_TO_QUALITY"
            confidence = 0.75
        
        # DIVIDEND SEASON: High dividend yield, low volatility
        elif self.features.get('dividend_yield', 0) > 0.025 and vix < 15:
            regime = "DIVIDEND_SEASON"
            confidence = 0.70
        
        # NORMAL CONSOLIDATION: Low momentum, normal volatility
        else:
            regime = "CONSOLIDATION"
            confidence = 0.60
        
        self.regime = regime
        self.confidence = confidence
        
        logger.info(f"Detected regime: {regime} (confidence: {confidence:.2%})")
        
        return regime, confidence
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _calculate_returns(self, ticker: str, days: int) -> float:
        """Calculate returns over specified period"""
        # PLACEHOLDER: Integrate with market data provider
        return 0.05
    
    def _calculate_spread(self, ticker1: str, ticker2: str) -> float:
        """Calculate spread between two securities"""
        # PLACEHOLDER: Integrate with market data provider
        return 0.02
    
    def _get_latest_level(self, ticker: str) -> float:
        """Get latest price level"""
        # PLACEHOLDER: Integrate with market data provider
        return 15.0
    
    def _calculate_correlation(self, ticker1: str, ticker2: str, days: int) -> float:
        """Calculate correlation between two assets"""
        # PLACEHOLDER: Integrate with historical data provider
        return 0.3
    
    def _estimate_yield_curve(self) -> float:
        """Estimate yield curve slope (10Y - 2Y)"""
        # PLACEHOLDER: Integrate with treasury rates provider
        return 0.5
    
    def _estimate_dividend_yield(self, ticker: str) -> float:
        """Estimate dividend yield"""
        # PLACEHOLDER: Integrate with financial data provider
        return 0.018
    
    def _classify_vix(self, vix_level: float) -> str:
        """Classify VIX level into regime"""
        if vix_level < 12:
            return "LOW"
        elif vix_level < 20:
            return "NORMAL"
        elif vix_level < 30:
            return "ELEVATED"
        else:
            return "HIGH"


# =============================================================================
# EXAMPLE MARKET REGIME PROFILES
# =============================================================================

REGIME_PROFILES = {
    "BULL": {
        "description": "Risk-on, strong equity momentum, falling yields",
        "equity_allocation": 0.70,
        "fixed_income_allocation": 0.25,
        "cash_allocation": 0.05,
        "characteristics": {
            "spy_momentum": "> +10%",
            "vix": "< 20",
            "bond_performance": "Negative",
            "credit_spreads": "Tightening",
        }
    },
    "BEAR": {
        "description": "Risk-off, weak equities, flight to quality",
        "equity_allocation": 0.30,
        "fixed_income_allocation": 0.60,
        "cash_allocation": 0.10,
        "characteristics": {
            "spy_momentum": "< -5%",
            "vix": "> 25",
            "bond_performance": "Positive",
            "credit_spreads": "Widening",
        }
    },
    "CORRECTION": {
        "description": "Temporary pullback in bull market",
        "equity_allocation": 0.50,
        "fixed_income_allocation": 0.40,
        "cash_allocation": 0.10,
        "characteristics": {
            "spy_momentum": "-2% to -5%",
            "vix": "20-30",
            "duration": "Temporary",
            "action": "Rebalance into weakness",
        }
    },
    "CONSOLIDATION": {
        "description": "Low volatility, sideways movement, indecision",
        "equity_allocation": 0.50,
        "fixed_income_allocation": 0.40,
        "cash_allocation": 0.10,
        "characteristics": {
            "spy_momentum": "-2% to +2%",
            "vix": "12-20",
            "ranges": "Established",
            "action": "Wait for breakout",
        }
    },
    "FLIGHT_TO_QUALITY": {
        "description": "Bonds outperforming stocks, credit stress",
        "equity_allocation": 0.25,
        "fixed_income_allocation": 0.65,
        "cash_allocation": 0.10,
        "characteristics": {
            "spy_performance": "Negative",
            "bond_performance": "Positive",
            "credit_spreads": "> 200bps",
            "action": "Reduce equity beta",
        }
    },
    "DIVIDEND_SEASON": {
        "description": "High dividend yields, income focus, low volatility",
        "equity_allocation": 0.65,
        "fixed_income_allocation": 0.25,
        "cash_allocation": 0.10,
        "characteristics": {
            "spy_div_yield": "> 2.5%",
            "vix": "< 15",
            "momentum": "Stable",
            "focus": "Income/Dividend stocks",
        }
    },
}
