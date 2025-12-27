"""
DEPRECATED NOTE: This document contained multi-asset scaffolding that has been removed. The current system is crypto-only (see regime_detection.py/allocation.py/executor.py). Sections below referencing traditional assets and orchestrators are legacy and kept for reference only.

MULTI-ASSET PORTFOLIO SYSTEM - QUICK REFERENCE

This document provides a quick overview of the system architecture and
how to integrate crypto (Kraken) and traditional assets (WealthSimple)
into a unified portfolio management system via SnapTrade.
"""

# =============================================================================
# FOLDER STRUCTURE
# =============================================================================

"""
Portfolio Management System/
└── app/
    └── trading/
        ├── crypto_regime_model.py              [EXISTING]
        │   - CryptoRegimeDetector class
        │   - Detects BULL/BEAR/UPTREND/DOWNTREND regimes
        │   - Uses: momentum, RSI, volume, on-chain metrics
        │
        ├── traditional_assets_regime.py         [NEW]
        │   - TraditionalAssetsRegimeDetector class
        │   - Detects BULL/BEAR/CORRECTION/CONSOLIDATION regimes
        │   - Uses: stock momentum, bond yields, VIX, credit spreads
        │
        ├── multi_asset_portfolio_manager.py     [NEW]
        │   - MultiAssetPortfolioManager class
        │   - Aggregates signals from both regime detectors
        │   - Computes optimal allocations
        │   - Manages rebalancing
        │
        ├── snaptrade_executor.py                [NEW]
        │   - SnapTradeExecutor class
        │   - Unified execution through SnapTrade API
        │   - Routes crypto trades to Kraken, traditional to WealthSimple
        │   - Tracks orders and fills
        │
        ├── portfolio_orchestrator.py            [NEW]
        │   - PortfolioOrchestrator class
        │   - Main entry point
        │   - Coordinates all components
        │   - Runs the control loop
        │
        └── INTEGRATION_GUIDE.md                 [NEW]
            - Detailed system architecture
            - Allocation profiles by regime
            - SnapTrade setup instructions
            - Implementation checklist
"""


# =============================================================================
# KEY CLASSES & RESPONSIBILITIES
# =============================================================================

"""
1. CryptoRegimeDetector (crypto_regime_model.py)
   ────────────────────────────────────────────
   Inputs:
   - Bitcoin/Ethereum price data
   - On-chain metrics (transfers, whale wallets)
   - Trading volume and volatility
   - RSI, MACD technical indicators
   
   Outputs:
   - Regime: BULL, BEAR, UPTREND, DOWNTREND
   - Confidence: 0.0 to 1.0
   - Features: Dict of all calculated metrics
   
   Example Output:
   {
       "regime": "BULL",
       "confidence": 0.82,
       "features": {
           "momentum_6m": 0.35,
           "rsi": 68,
           "volume_trend": "increasing"
       }
   }


2. TraditionalAssetsRegimeDetector (traditional_assets_regime.py)
   ───────────────────────────────────────────────────────────────
   Inputs:
   - Stock index data (SPY, QQQ, Russell 2000)
   - Bond yields (10Y Treasury, credit spreads)
   - Volatility (VIX, MOVE index)
   - Macroeconomic data (USD, commodities)
   
   Outputs:
   - Regime: BULL, BEAR, CORRECTION, CONSOLIDATION, FLIGHT_TO_QUALITY
   - Confidence: 0.0 to 1.0
   - Features: Dict of market metrics
   
   Example Output:
   {
       "regime": "CONSOLIDATION",
       "confidence": 0.60,
       "features": {
           "spy_momentum": 0.02,
           "vix": 18.5,
           "yield_curve": 0.5
       }
   }


3. MultiAssetPortfolioManager (multi_asset_portfolio_manager.py)
   ──────────────────────────────────────────────────────────────
   Inputs:
   - Crypto regime signal from CryptoRegimeDetector
   - Traditional regime signal from TraditionalAssetsRegimeDetector
   - Current portfolio positions
   
   Processing:
   - Aggregate both signals into unified regime (AGGRESSIVE, DEFENSIVE, BALANCED)
   - Select allocation profile based on regime
   - Distribute within crypto (BTC, ETH, ALT)
   - Distribute within traditional (SPY, QQQ, BND, TLT, etc.)
   - Compute rebalancing trades when drift > threshold
   
   Outputs:
   - PortfolioAllocation with target weights
   - List of rebalancing trades
   
   Example Output:
   {
       "regime_profile": "BALANCED",
       "crypto_allocation": 0.15,
       "traditional_allocation": 0.75,
       "cash_allocation": 0.10,
       "allocations": [
           {"ticker": "BTC", "weight": 0.09, "value": $45000},
           {"ticker": "ETH", "weight": 0.04, "value": $20000},
           {"ticker": "SPY", "weight": 0.35, "value": $175000},
           ...
       ]
   }


4. SnapTradeExecutor (snaptrade_executor.py)
   ──────────────────────────────────────────
   Inputs:
   - Trade list from MultiAssetPortfolioManager
   - Order details (ticker, side, quantity, price)
   
   Processing:
   - Groups trades by broker (Kraken vs WealthSimple)
   - Submits to SnapTrade API
   - Crypto trades → Kraken account via SnapTrade
   - Traditional trades → WealthSimple account via SnapTrade
   - Monitors order fills
   
   Outputs:
   - ExecutionReport with submitted/filled counts
   - Order history
   
   Example Output:
   {
       "execution_id": "uuid",
       "orders_submitted": 8,
       "orders_filled": 8,
       "total_value_traded": 45000,
       "success": true
   }


5. PortfolioOrchestrator (portfolio_orchestrator.py)
   ────────────────────────────────────────────────
   The "Brain" that coordinates everything:
   
   Main Loop (runs every 5 minutes):
   1. Update market data
   2. Detect crypto regime
   3. Detect traditional regime
   4. Pass signals to portfolio manager
   5. Check if rebalancing needed
   6. Execute rebalancing if needed
   7. Log status and performance
   
   Provides:
   - System status and health
   - Portfolio performance metrics
   - Manual controls (pause, resume, force rebalance)
"""


# =============================================================================
# DATA FLOW EXAMPLE
# =============================================================================

"""
SCENARIO: Crypto rallies while stocks consolidate

1. Market Data Update
   ├─ BTC: $45,000 (+15% in 6 months)
   ├─ ETH: $2,800 (+12% in 6 months)
   ├─ SPY: $450 (+2% in 6 months)
   └─ VIX: 18

2. Regime Detection
   ├─ CryptoRegimeDetector outputs:
   │  - Regime: BULL
   │  - Confidence: 82%
   └─ TraditionalAssetsRegimeDetector outputs:
      - Regime: CONSOLIDATION
      - Confidence: 60%

3. Portfolio Manager Processing
   ├─ Aggregate regime: BALANCED (crypto bullish, traditional neutral)
   ├─ Select allocation for BALANCED regime:
   │  - Crypto: 15% (moderate exposure due to crypto strength)
   │  - Traditional: 65% (maintain traditional exposure)
   │  - Cash: 10%
   └─ Compute target weights:
      - BTC: 9% of portfolio ($45,000)
      - ETH: 4% of portfolio ($20,000)
      - SPY: 35% of portfolio ($175,000)
      - BND: 15% of portfolio ($75,000)
      - TLT: 10% of portfolio ($50,000)
      - Cash: 10% of portfolio ($50,000)

4. Rebalancing Check
   - Current portfolio drifts to:
     - BTC: 12% (gained +300 bps from price appreciation)
     - SPY: 32% (lost -300 bps from underperformance)
   - Drift detected: BTC +3%, SPY -3% (exceeds 5% threshold? No, wait)

5. If Rebalancing Triggered:
   - Generate trades:
     - SELL 0.5 BTC (~$22,500) [Reduce crypto]
     - BUY 50 SPY (~$22,500) [Increase traditional]
   
6. Execution via SnapTrade:
   - Crypto trade → Kraken account
     - SELL 0.5 BTC at market
     - SnapTrade order ID: xyz123
   - Traditional trade → WealthSimple account
     - BUY 50 SPY at market
     - SnapTrade order ID: abc456
   
7. Order Monitoring:
   - Monitor fills every 10 seconds
   - Crypto filled: 0.5 BTC @ $44,950 average
   - Traditional filled: 50 SPY @ $449.80 average
   
8. Results:
   - Portfolio rebalanced
   - $22,468 in crypto proceeds moved to traditional
   - New allocation closer to targets
   - Transaction costs: ~0.15% (within acceptable range)
"""


# =============================================================================
# INTEGRATION CHECKLIST
# =============================================================================

"""
BEFORE GOING LIVE:

□ Regime Detectors
  □ CryptoRegimeDetector: Test on 2 years of historical data
  □ TraditionalAssetsRegimeDetector: Test on 5 years of data
  □ Validate regime classifications vs actual market movements
  
□ Portfolio Manager
  □ Test allocation computation with various signal combinations
  □ Validate that allocations sum to 100%
  □ Test rebalancing trigger logic
  □ Verify constraints (max crypto, min cash)
  
□ SnapTrade Integration
  □ Create SnapTrade account and link both brokers
  □ Test authentication (client_id, consumer_key)
  □ Verify Kraken and WealthSimple accounts are linked
  □ Test API calls in sandbox mode
  □ Test order placement and fills
  □ Verify trade routing (crypto → Kraken, traditional → WealthSimple)
  
□ Executor
  □ Test market order execution
  □ Test limit order execution
  □ Test order monitoring and fill detection
  □ Test batch execution with mixed asset types
  □ Verify execution reports are accurate
  
□ Orchestrator
  □ Test main control loop
  □ Test regime detection integration
  □ Test allocation computation
  □ Test rebalancing execution
  □ Test status reporting
  
□ Risk Management
  □ Set appropriate position limits
  □ Test stop-loss mechanisms
  □ Verify logging and audit trails
  □ Test error handling
  
□ Monitoring & Alerts
  □ Set up performance monitoring
  □ Create alerts for regime changes
  □ Create alerts for large deviations
  □ Create alerts for execution failures
  
□ Deployment
  □ Choose hosting environment (cloud, local, etc.)
  □ Set up automated scheduling (cron, etc.)
  □ Configure logging to persistent storage
  □ Set up backup and disaster recovery
  □ Document operational procedures
"""


# =============================================================================
# KEY METRICS TO TRACK
# =============================================================================

"""
Portfolio Level:
- Total return (vs initial investment)
- YTD return
- Monthly return
- Volatility (annualized)
- Sharpe ratio
- Max drawdown
- Win rate (months with positive returns)

Allocation Level:
- Crypto allocation (target vs actual)
- Traditional allocation (target vs actual)
- Cash allocation (target vs actual)
- Allocation drift by asset

Regime Level:
- Regime accuracy (how often detector is correct)
- Regime persistence (how long regimes last)
- Regime changes detected per month

Execution Level:
- Orders submitted vs filled
- Average fill price vs market price
- Execution time (submission to fill)
- Transaction costs (bps)
- Slippage (actual vs expected)

Risk Level:
- VaR (Value at Risk)
- Correlation between asset classes
- Concentration risk
- Liquidity risk
"""


# =============================================================================
# DEPLOYMENT OPTIONS
# =============================================================================

"""
Option 1: Cloud Deployment (Recommended)
─────────────────────────────────────────
Platform: AWS Lambda, Google Cloud Functions, or Azure Functions
Trigger: CloudWatch Events (every 5 minutes)
Costs: ~$20-50/month
Pros:
- Reliable uptime
- Auto-scaling
- Built-in monitoring
- Easy to manage

Option 2: VPS/Cloud Server
──────────────────────────
Platform: DigitalOcean, Linode, AWS EC2
Trigger: Cron job (every 5 minutes)
Costs: $5-50/month
Pros:
- More control
- Custom environment
- Can run multiple strategies

Option 3: Local Computer
───────────────────────
Platform: Windows/Mac/Linux
Trigger: Task Scheduler (Windows) or cron (Mac/Linux)
Costs: Electricity only
Pros:
- Free
- Highest control
- No latency
Cons:
- Requires always-on computer
- Network issues can break execution


Recommended Setup:
──────────────────
1. Use AWS Lambda for main orchestrator
2. Trigger every 5 minutes
3. Use CloudWatch for monitoring
4. Log to S3 for persistence
5. Alert via SNS (email/SMS)
6. Use RDS (PostgreSQL) for state management
7. Cost: ~$30-50/month
"""


# =============================================================================
# FILE SIZES & COMPLEXITY
# =============================================================================

"""
crypto_regime_model.py (EXISTING)
├─ Size: ~500 lines
├─ Classes: 1 main class + helpers
└─ Complexity: Medium

traditional_assets_regime.py (NEW)
├─ Size: ~600 lines
├─ Classes: 1 main class + helpers
└─ Complexity: Medium

multi_asset_portfolio_manager.py (NEW)
├─ Size: ~400 lines
├─ Classes: 1 main class + dataclasses
└─ Complexity: Medium

snaptrade_executor.py (NEW)
├─ Size: ~450 lines
├─ Classes: 1 main class + dataclasses
└─ Complexity: Medium

portfolio_orchestrator.py (NEW)
├─ Size: ~350 lines
├─ Classes: 1 main class
└─ Complexity: Low (mostly coordination)

TOTAL: ~2,300 lines of core code
(Plus tests, documentation, utilities)
"""


# =============================================================================
# SAMPLE COMMAND TO RUN
# =============================================================================

"""
# Start the portfolio orchestrator
python -m app.trading.portfolio_orchestrator

# Run a single iteration for testing
python -c "
from app.trading.portfolio_orchestrator import PortfolioOrchestrator

orchestrator = PortfolioOrchestrator(
    initial_portfolio_value=500_000,
    snaptrade_client_id='YOUR_ID',
    snaptrade_consumer_key='YOUR_KEY',
    kraken_account_id='kraken_123',
    wealthsimple_account_id='wealth_456'
)

orchestrator.start()
orchestrator.step()

status = orchestrator.get_status()
print(f'System Status: {status}')

orchestrator.stop()
"
"""


# =============================================================================
# NEXT STEPS
# =============================================================================

"""
1. Review the system architecture and data flow
2. Understand each component's role and responsibilities
3. Implement the TODO items in the code
4. Set up SnapTrade integration
5. Test with paper trading (simulated)
6. Backtest on historical data
7. Deploy to production
8. Monitor performance and iterate

See INTEGRATION_GUIDE.md for detailed implementation instructions.
"""
