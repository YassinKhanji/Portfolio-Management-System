"""
Configuration

Environment variables and application settings.
Based on SYSTEM_SPECIFICATIONS.md
"""

from pydantic_settings import BaseSettings
from pydantic import field_validator
import json
from functools import lru_cache
import os
from typing import List, Union


class Settings(BaseSettings):
    """Application settings from environment variables"""
    
    # ============================================================================
    # API Configuration
    # ============================================================================
    API_TITLE: str = "Portfolio Management Trading System"
    API_VERSION: str = "1.0.0"
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    
    # ============================================================================
    # Database Configuration (Neon PostgreSQL)
    # ============================================================================
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/portfolio")
    DATABASE_POOL_SIZE: int = 10  # Max connections
    DATABASE_POOL_OVERFLOW: int = 5
    
    # Data Retention Policies
    TRANSACTION_RETENTION_DAYS: int = 30  # Hard delete after 30 days
    PORTFOLIO_SNAPSHOT_RETENTION_DAYS: int = 365  # Keep 1 year
    SNAPSHOT_AGGREGATION_DAYS: int = 90  # Aggregate to daily after 90 days
    SYSTEM_LOG_RETENTION_DAYS: int = 90  # 90 days
    BACKUP_RETENTION_DAYS: int = 7  # 7 day rolling backup
    
    # ============================================================================
    # Authentication & JWT
    # ============================================================================
    JWT_SECRET: str = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # Access token: 30 minutes
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7  # Refresh token: 7 days
    JWT_REMEMBER_ME_EXPIRE_DAYS: int = 30  # "Remember me" token: 30 days
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
    
    # ============================================================================
    # CORS Configuration
    # ============================================================================
    # Accepts: JSON array '["url1","url2"]', comma-separated 'url1,url2', or single URL
    CORS_ORIGINS: Union[str, List[str]] = "http://localhost:5173"
    
    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS_ORIGINS from various formats"""
        if v is None or v == "":
            return ["http://localhost:5173"]
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            # Try JSON array first
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            # Comma-separated or single URL
            return [origin.strip().strip('"').strip("'") for origin in v.split(",") if origin.strip()]
        return [str(v)]
    
    # ============================================================================
    # Logging Configuration
    # ============================================================================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    LOG_ROTATION_SIZE: int = 10_485_760  # 10 MB
    LOG_RETENTION_COUNT: int = 20
    
    # ============================================================================
    # SnapTrade Integration
    # ============================================================================
    SNAPTRADE_CLIENT_ID: str = os.getenv("SNAPTRADE_CLIENT_ID", "")
    SNAPTRADE_CLIENT_SECRET: str = os.getenv("SNAPTRADE_CLIENT_SECRET", "")
    SNAPTRADE_SANDBOX: bool = os.getenv("SNAPTRADE_SANDBOX", "True").lower() == "true"
    SNAPTRADE_REDIRECT_URI: str = os.getenv("SNAPTRADE_REDIRECT_URI", "")
    SNAPTRADE_APP_URL: str = os.getenv("SNAPTRADE_APP_URL", "https://app.snaptrade.com")
    
    # Supported Brokers
    SUPPORTED_CRYPTO_EXCHANGES: List[str] = ["kraken"]  # Kraken only
    SUPPORTED_EQUITIES_BROKERS: List[str] = ["wealthsimple", "wealthsimple_trade"]  # WS only
    
    # Order Execution
    LIMIT_ORDER_TIMEOUT_SECONDS: int = 300  # 5 minutes: convert to market if not filled
    MAX_RETRIES: int = 10  # Retry logic: 10 attempts before giving up
    RETRY_DELAY_SECONDS: int = 5
    
    # ============================================================================
    # Rebalancing Configuration
    # ============================================================================
    # Every 7/3 days = every 2.33 days (3 times per week)
    # Starting Sunday 00:00 AM EST
    REBALANCE_SCHEDULE: List[dict] = [
        {"day": "sunday", "hour": 0, "minute": 0},      # Sunday 00:00 AM EST
        {"day": "tuesday", "hour": 16, "minute": 0},    # Tuesday 16:00 (4:00 PM) EST
        {"day": "friday", "hour": 8, "minute": 0},      # Friday 08:00 (8:00 AM) EST
    ]
    
    # ============================================================================
    # Risk Profiles & Asset Allocation
    # ============================================================================
    RISK_PROFILES: dict = {
        "Conservative": {"crypto": 0.10, "stocks": 0.55, "cash": 0.35},
        "Balanced": {"crypto": 0.20, "stocks": 0.60, "cash": 0.20},
        "Aggressive": {"crypto": 0.35, "stocks": 0.55, "cash": 0.10},
    }
    DEFAULT_RISK_PROFILE: str = "Balanced"
    
    # ============================================================================
    # Risk Limits
    # ============================================================================
    MAX_PORTFOLIO_DRAWDOWN: float = -0.30  # -30%
    DRAWDOWN_WARNING_THRESHOLD: float = -0.20  # Alert at -20%
    LEVERAGE_ALLOWED: float = 1.0  # 1x only, no margin
    
    # ============================================================================
    # Currency & Exchange Rates
    # ============================================================================
    PRIMARY_CURRENCY: str = "CAD"
    OANDA_API_KEY: str = os.getenv("OANDA_API_KEY", "")
    EXCHANGE_RATE_UPDATE_FREQUENCY_SECONDS: int = 3600  # Real-time (hourly)
    
    # ============================================================================
    # Market Data APIs
    # ============================================================================
    TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")  # Free tier: 800 requests/day
    
    # ============================================================================
    # Performance Metrics Configuration
    # ============================================================================
    # Return Calculations
    DEFAULT_RETURN_METHOD: str = "TWR"  # Time-Weighted Return (default), also support MWR
    AVAILABLE_RETURN_METHODS: List[str] = ["TWR", "MWR"]
    AVAILABLE_RETURN_PERIODS: List[str] = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"]
    
    # Risk Metrics
    RISK_FREE_RATE: float = 0.0  # Canadian T-Bill rate: 0%
    AVAILABLE_LOOKBACK_PERIODS: List[int] = [30, 90, 365]  # days
    MIN_HISTORY_REQUIRED_DAYS: int = 30  # Minimum 30 days before showing Sharpe/Sortino/Calmar
    
    # Benchmarks
    CRYPTO_BENCHMARK: str = "BTC"  # Bitcoin as primary crypto benchmark
    EQUITIES_BENCHMARK: str = "SPY"  # S&P 500 as primary equities benchmark
    
    # Portfolio Snapshots
    SNAPSHOT_FREQUENCY_HOURS: int = 4  # Every 4 hours
    SNAPSHOT_GRANULARITY: dict = {
        "1D": "hourly",
        "5D": "hourly",
        "1W": "daily",
        "1M": "daily",
        "3M": "weekly",
        "6M": "weekly",
        "YTD": "weekly",
        "1Y": "weekly",
    }
    
    # ============================================================================
    # Email Notifications (Gmail SMTP)
    # ============================================================================
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "yassinkhanji9@gmail.com")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")  # Gmail app-specific password
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "yassinkhanji9@gmail.com")
    SMTP_FROM_NAME: str = "Portfolio Management System"
    
    # Alert Notifications
    ENABLED_ALERT_TYPES: List[str] = [
        "rebalance_completed",
        "regime_change",
        "emergency_stop_triggered",
        "health_check_failed",
        "api_error",
        "transfer_needed",
        "drawdown_warning",
    ]
    SEND_DAILY_DIGEST: bool = True
    DAILY_DIGEST_TIME: str = "08:00"  # 8:00 AM EST
    SEND_MONTHLY_REPORT: bool = True
    MONTHLY_REPORT_DAY: int = 1  # First day of month
    
    # ============================================================================
    # Background Jobs Schedule
    # ============================================================================
    # Market Data Refresh: Every 4 hours
    MARKET_DATA_REFRESH_HOURS: int = 4
    
    # Health Check: Every hour
    HEALTH_CHECK_FREQUENCY_HOURS: int = 1
    
    # Portfolio Snapshot: Every 4 hours
    PORTFOLIO_SNAPSHOT_FREQUENCY_HOURS: int = 4
    
    # ============================================================================
    # Feature Flags & Removed Features
    # ============================================================================
    ENABLE_MANUAL_REBALANCE: bool = False  # Removed: clients cannot trigger manual rebalance
    ENABLE_DEPOSIT_FUNDS_BUTTON: bool = False  # Removed: client cannot deposit
    ENABLE_REPORTS_DOCUMENTS: bool = False  # Removed: no document storage
    ENABLE_TAX_LOSS_HARVESTING: bool = False  # Not implemented
    ENABLE_BACKTESTING: bool = False  # Not implemented
    
    # ============================================================================
    # Frontend/UX Configuration
    # ============================================================================
    DARK_MODE_DEFAULT: bool = True
    MOBILE_FIRST: bool = True
    SHOW_TECHNICAL_ERRORS_ADMIN: bool = True  # Show stack traces to admins only
    SHOW_TECHNICAL_ERRORS_CLIENT: bool = False  # Show user-friendly messages to clients
    
    # ============================================================================
    # Performance & Optimization
    # ============================================================================
    # Caching Strategy
    REGIME_CACHE_HOURS: int = 4
    MARKET_DATA_CACHE_HOURS: int = 4
    EXCHANGE_RATE_CACHE_HOURS: int = 1
    
    # Performance Targets
    TARGET_DASHBOARD_LOAD_SECONDS: float = 2.0
    TARGET_API_RESPONSE_SECONDS: float = 0.5
    TARGET_REBALANCE_EXECUTION_SECONDS: float = 30.0
    
    # ============================================================================
    # Admin Configuration
    # ============================================================================
    # Owner/Administrator account email (used for owner-gated actions)
    # Prefer OWNER_EMAIL if provided, else fallback to ADMIN_EMAIL for backward compatibility
    ADMIN_EMAIL: str = os.getenv("OWNER_EMAIL", os.getenv("ADMIN_EMAIL", "yassinkhanji9@gmail.com"))
    ALLOW_ADMIN_ACCOUNT_SUSPENSION: bool = True
    ALLOW_ADMIN_AUDIT_LOG_ACCESS: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
