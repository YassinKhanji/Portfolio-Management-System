# Portfolio Management Trading System

A production-ready automated crypto portfolio management system with regime-based allocation, real-time rebalancing, and SnapTrade integration for Canadian investors.

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Neon Postgres database (connection string)
- SnapTrade API credentials (for Canadian investment accounts)

### Installation

1. **Clone & Navigate**
   ```bash
   cd "Portfolio Management System"
   ```

2. **Create Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Neon DATABASE_URL, API keys, SnapTrade credentials
   ```

5. **Start the Backend**
   ```bash
   python -m app.main
   ```

Server starts at `http://localhost:8000`

## 📋 System Architecture

```
Trading System (Python/FastAPI)
├── Regime Detection Engine
│   ├── Market regime classification (HODL, Risk Off, BTC Season, etc.)
│   ├── Volatility estimation (Yang-Zhang)
│   └── Bitcoin/Altcoin dominance tracking
├── Portfolio Allocation Engine
│   ├── Regime-based constraints
│   ├── Distributionally Robust Optimization (DRO)
│   └── Staggered rebalancing (3-bucket approach)
├── Execution Engine
│   ├── SnapTrade API integration
│   ├── Order placement & management
│   └── Trade logging & audit
└── Monitoring & Alerts
    ├── System health checks
    ├── Alert management (7+ types)
    └── Performance tracking
```

## 📁 Project Structure

```
Portfolio Management System/
├── app/                      # Main application
│   ├── core/                # Configuration, logging, alerts
│   │   ├── config.py        # Pydantic settings
│   │   ├── logging.py       # Structured logging with rotation
│   │   └── alerts.py        # Alert system (7+ types, 4 levels)
│   ├── models/              # Database layer
│   │   └── database.py      # SQLAlchemy ORM models
│   ├── trading/             # Trading logic
│   │   ├── regime_detection.py     # Crypto regime detection (CCXT → Kraken)
│   │   ├── allocation.py           # Portfolio optimization
│   │   ├── indicators.py           # Technical indicators
│   │   └── executor.py             # Trade execution
│   ├── services/            # External integrations
│   │   ├── snaptrade_integration.py  # SnapTrade API client
│   │   ├── web_app_client.py        # Frontend API client
│   │   └── market_data.py           # Market data service
│   ├── jobs/                # Background jobs
│   │   ├── scheduler.py     # APScheduler configuration
│   │   ├── data_refresh.py  # Market data update job
│   │   ├── daily_rebalance.py      # Rebalancing job
│   │   └── health_check.py         # System health monitoring
│   └── main.py              # FastAPI application entry point
├── tests/                   # Unit & integration tests
│   ├── test_model.py
│   └── test_integration.py
├── requirements.txt         # Python dependencies
├── .env.example            # Environment template
└── docker-compose.yml      # PostgreSQL + Redis (optional)
```

## 🔑 Key Features

### 1. **Regime Detection**
- Market regime classification using volatility & direction indicators
- Yang-Zhang volatility estimator for accurate risk assessment
- Tickers: Total market, top altcoins, Bitcoin/Ethereum dominance, stablecoins

**Regimes:**
- **HODL**: Market indecisive (tight volatility)
- **Risk Off**: Market declining (flight to stablecoins)
- **BTC Season**: Bitcoin rallying, altcoins lagging
- **Altcoin Season + ETH Season**: Altcoins & ETH rallying
- **Risk On**: Broad market rally with balanced growth

### 2. **Portfolio Allocation**
- Regime-specific asset constraints
- Distributionally Robust Optimization (DRO) with scipy
- Multi-objective optimization (Sharpe, Sortino, Calmar, Starr ratios)
- Staggered 3-bucket rebalancing for smooth transitions

**Allocation Rules:**
| Regime | BTC | ETH | ALT | STABLE |
|--------|-----|-----|-----|--------|
| Risk Off | 0-20% | 0-20% | 0-10% | 60-100% |
| BTC Season | 50-100% | 10-40% | 0-10% | 0-30% |
| Altcoin Season | 10-40% | 30-60% | 20-50% | 0-20% |
| Risk On | 20-40% | 20-40% | 20-40% | 0-20% |
| HODL | 25% | 25% | 25% | 25% |

### 3. **Execution Layer**
- SnapTrade API integration for Canadian accounts
- Supports all Canadian brokers (Questrade, Interactive Brokers, etc.)
- Account & holdings retrieval
- Market & limit order execution
- Trade logging & audit trail

### 4. **Monitoring & Alerts**
**Alert Types** (7):
- `rebalance_needed`: Portfolio drift exceeds threshold
- `trade_failed`: Order execution error
- `regime_change`: Market regime changed
- `drift_alert`: Asset allocation drift
- `data_refresh_failed`: Market data update failure
- `health_check_failed`: System health issue
- `emergency_stop_triggered`: Emergency stop activated

**Severity Levels** (4):
- `info`: Informational (regime change)
- `warning`: Action recommended (drift alert)
- `critical`: Immediate action needed (trade failed)
- `emergency`: System stopped (emergency stop)

### 5. **Background Jobs**
- **Data Refresh** (4-hour intervals): Fetch latest market data
- **Daily Rebalance** (daily at 9 AM UTC): Execute portfolio rebalancing
- **Health Check** (hourly): Monitor system components

## 🔌 API Endpoints

### Rebalancing
```
POST /api/rebalance/{user_id}
POST /api/rebalance/all
GET /api/portfolio/calculate/{user_id}
```

### Market Data
```
GET /api/regime/status
GET /api/system/health
```

### Monitoring
```
GET /api/system/logs
GET /api/system/alerts
```

### Emergency Controls
```
POST /api/system/emergency-stop
POST /api/system/emergency-stop/reset
```

## 📊 Database Schema

**Users**
- `id`, `email`, `snaptrade_token`, `risk_profile`

**Accounts** (SnapTrade)
- `id`, `user_id`, `account_type`, `broker`, `balance`

**Holdings**
- `id`, `account_id`, `symbol`, `quantity`, `cost_basis`

**Trades**
- `id`, `account_id`, `symbol`, `side`, `quantity`, `price`, `timestamp`

**Alerts**
- `id`, `user_id`, `type`, `severity`, `message`, `created_at`, `read`

**MarketData**
- `id`, `symbol`, `timestamp`, `open`, `high`, `low`, `close`, `volume`

## 🛠️ Configuration

### Environment Variables
```env
# Database
DATABASE_URL=postgresql://user:password@localhost/trading_system

# API Keys
SNAPTRADE_API_KEY=your_api_key
SNAPTRADE_CLIENT_ID=your_client_id

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/trading_system.log

# Rebalancing
REBALANCE_HOUR=9
REBALANCE_MINUTE=0
DRIFT_THRESHOLD=0.05

# Market Data
DATA_REFRESH_INTERVAL_HOURS=4
```

## 📈 Performance Metrics

The system tracks:
- **Total Return**: Cumulative portfolio return
- **Annual Return**: Annualized return
- **Volatility**: Annualized volatility
- **Sharpe Ratio**: Return per unit of risk
- **Max Drawdown**: Largest peak-to-trough decline
- **Win Rate**: Percentage of profitable trades

## 🔒 Security

- Encrypted SnapTrade tokens (at-rest)
- API key rotation support
- Audit logs for all trades
- Emergency stop capability
- Database transaction logging

## 🚀 Deployment

### Docker
```bash
docker-compose up -d
```

### DigitalOcean (Recommended)
```bash
# Run startup script
./startup.sh
```

### Manual
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 📚 Documentation

- [BACKEND_README.md](BACKEND_README.md) - API documentation
- [DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) - Production deployment
- [QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) - Developer cheat sheet
- [Architectures/](Architectures/) - System architecture diagrams

## 🧪 Testing

```bash
# Run tests
pytest tests/

# With coverage
pytest --cov=app tests/
```

## 📞 Support

For issues or questions:
1. Check [QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md)
2. Review system logs in `logs/`
3. Check alerts via `/api/system/alerts`
4. Verify system health via `/api/system/health`

## 📄 License

Proprietary - Portfolio Management System
