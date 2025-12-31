"""
Database Models

SQLAlchemy ORM models for the shared database between Trading System and Web App.
Based on SYSTEM_SPECIFICATIONS.md

Tables:
- Users (user profiles, SnapTrade tokens, risk profiles, alert preferences)
- Connections (SnapTrade account connections - Kraken, Wealthsimple)
- Positions (current holdings per asset class)
- PortfolioSnapshots (historical portfolio values for charts - 4 hourly)
- Transactions (trade execution history - 1 month retention)
- RiskProfiles (client custom allocations)
- Regimes (market regime history)
- Logs (system audit trail - 90 day retention)
- Alerts (system and user alerts - 30 day retention)
- AlertPreferences (client notification preferences)
- SystemStatus (overall system health)
"""

from sqlalchemy import create_engine, Column, String, Float, DateTime, Boolean, Integer, JSON, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
from typing import Optional
import os
from dotenv import load_dotenv
import uuid
import logging

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Database URL (set in environment)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost/portfolio_management"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,  # Verify connections before reusing
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================================
# Encryption Helpers for Sensitive Data
# ============================================================================

def encrypt_secret(plaintext: str) -> str:
    """Encrypt a sensitive value (like snaptrade_user_secret) before storing."""
    if not plaintext:
        return plaintext
    
    try:
        from app.core.security import encrypt_value, is_encrypted
        # Don't double-encrypt
        if is_encrypted(plaintext):
            return plaintext
        return encrypt_value(plaintext)
    except ImportError:
        logger.warning("Encryption module not available - storing plaintext")
        return plaintext
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return plaintext


def decrypt_secret(encrypted: str) -> str:
    """Decrypt a sensitive value when reading from database."""
    if not encrypted:
        return encrypted
    
    try:
        from app.core.security import decrypt_value, is_encrypted
        # Only decrypt if it looks encrypted
        if not is_encrypted(encrypted):
            return encrypted
        return decrypt_value(encrypted)
    except ImportError:
        logger.warning("Encryption module not available - returning as-is")
        return encrypted
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return encrypted


# ============================================================================
# Models
# ============================================================================

class User(Base):
    """User account and preferences"""
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))  # UUID
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    
    # User role and status
    role = Column(String, default="client")  # 'admin' or 'client'
    active = Column(Boolean, default=False)  # Activated after first successful login
    first_login_at = Column(DateTime, nullable=True)
    last_login = Column(DateTime, nullable=True)
    
    # SnapTrade integration
    snaptrade_token = Column(String, nullable=True)
    snaptrade_user_id = Column(String, nullable=True)
    snaptrade_linked = Column(Boolean, default=False)
    
    # Onboarding
    onboarding_completed = Column(Boolean, default=False)
    
    # Risk Profile (Conservative, Balanced, Aggressive)
    risk_profile = Column(String, default="Balanced")
    rebalance_frequency = Column(String, default="weekly")
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Additional metadata
    metadata_json = Column(JSON, default={})


class Connection(Base):
    """SnapTrade account connections (Kraken for crypto, Wealthsimple for equities)"""
    __tablename__ = "connections"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True)  # Foreign key to users
    
    # SnapTrade details
    snaptrade_user_id = Column(String, nullable=False)
    _snaptrade_user_secret = Column("snaptrade_user_secret", String, nullable=False)  # Encrypted at rest
    
    # Account type and broker
    account_type = Column(String, nullable=False)  # 'crypto' or 'equities'
    broker = Column(String, nullable=False)  # 'kraken', 'wealthsimple', 'wealthsimple_trade'
    
    # Connection status
    is_connected = Column(Boolean, default=False)
    connection_status = Column(String, default="pending")  # pending, connected, failed, disconnected
    
    # Account details from SnapTrade
    account_id = Column(String, nullable=True)  # SnapTrade account ID
    account_balance = Column(Float, default=0.0)  # Current balance
    balance_currency = Column(String, default="CAD")  # Currency of account
    
    # Timestamps
    connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    @property
    def snaptrade_user_secret(self) -> str:
        """Decrypt the secret when reading."""
        return decrypt_secret(self._snaptrade_user_secret) if self._snaptrade_user_secret else None
    
    @snaptrade_user_secret.setter
    def snaptrade_user_secret(self, value: str):
        """Encrypt the secret when storing."""
        self._snaptrade_user_secret = encrypt_secret(value) if value else None


class Position(Base):
    """Current portfolio holdings per asset class"""
    __tablename__ = "positions"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True)
    
    # Asset details
    symbol = Column(String, nullable=False)  # 'BTC', 'ETH', 'AAPL', etc.
    
    # Position data
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # Current price in CAD
    market_value = Column(Float, nullable=False)  # quantity * price in CAD
    cost_basis = Column(Float, nullable=True)  # Average purchase price per unit
    
    # Last order tracking
    last_order_time = Column(DateTime, nullable=True)  # When the last order was placed
    last_order_side = Column(String, nullable=True)  # BUY, SELL, or HOLD (no recent orders)
    
    # Allocation tracking
    allocation_percentage = Column(Float, default=0.0)
    target_percentage = Column(Float, default=0.0)
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), index=True)
    
    # Metadata
    metadata_json = Column(JSON, default={})


class PortfolioSnapshot(Base):
    """Historical portfolio values for performance charts - taken every 4 hours"""
    __tablename__ = "portfolio_snapshots"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True)
    
    # Portfolio totals
    total_value = Column(Float, nullable=False)  # Total portfolio value in CAD
    crypto_value = Column(Float, default=0.0)
    stocks_value = Column(Float, default=0.0)
    cash_value = Column(Float, default=0.0)
    
    # Returns
    daily_return = Column(Float, default=0.0)  # Daily P&L in CAD
    daily_return_pct = Column(Float, default=0.0)  # Daily return percentage
    
    # Positions snapshot (JSON for easy historical access)
    positions_snapshot = Column(JSON, default={})  # {"BTC": {...}, "ETH": {...}, ...}
    
    # Allocation snapshot
    allocation_snapshot = Column(JSON, default={})  # {"crypto": 0.20, "stocks": 0.60, "cash": 0.20}
    
    # Timestamp (in user's local timezone)
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    # For aggregation
    aggregation_level = Column(String, default="4h")  # 4h, daily, weekly (after 90 days)


class Transaction(Base):
    """Trade execution history - kept for 1 month"""
    __tablename__ = "transactions"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True)
    
    # Trade details
    symbol = Column(String, nullable=False)
    
    # Order details
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # Execution price in original currency
    side = Column(String, nullable=False)  # 'BUY' or 'SELL'
    
    # SnapTrade tracking
    snaptrade_order_id = Column(String, nullable=True, index=True)
    status = Column(String, default="pending")  # pending, filled, partially_filled, failed, cancelled
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    executed_at = Column(DateTime, nullable=True)
    
    # Metadata
    metadata_json = Column(JSON, default={})


class RiskProfile(Base):
    """Client custom allocation settings"""
    __tablename__ = "risk_profiles"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True, unique=True)
    
    # Allocation: [Crypto, Stocks, Gold/Cash]
    crypto_allocation = Column(Float, nullable=False)  # 0.0 to 1.0
    stocks_allocation = Column(Float, nullable=False)  # 0.0 to 1.0
    cash_allocation = Column(Float, nullable=False)  # 0.0 to 1.0
    
    # Additional settings
    questionnaire_responses = Column(JSON, default={})  # Store answers to risk questionnaire
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Regime(Base):
    """Market regime history"""
    __tablename__ = "regimes"
    
    id = Column(String, primary_key=True)  # UUID
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    # Crypto regime (from regime detection model)
    crypto_regime = Column(String, nullable=False)  # HODL, Risk Off, BTC Season, Altcoin Season, Risk On
    crypto_confidence = Column(Float, default=0.0)
    
    # Equities regime (to be implemented)
    equities_regime = Column(String, nullable=True)  # BULL, BEAR, CORRECTION, etc.
    equities_confidence = Column(Float, default=0.0)
    
    # Combined signal
    combined_signal = Column(String, nullable=True)
    
    # Detailed metrics (JSON)
    indicators = Column(JSON, default={})  # All calculated indicators


class Log(Base):
    """System audit trail - 90 day retention"""
    __tablename__ = "logs"
    
    id = Column(String, primary_key=True)  # UUID
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    # Log details
    level = Column(String, nullable=False)  # 'debug', 'info', 'warning', 'error', 'critical'
    message = Column(Text, nullable=False)
    component = Column(String, nullable=False)  # e.g., 'auth', 'rebalancing', 'snaptrade'
    
    # Associated entities
    user_id = Column(String, nullable=True, index=True)  # Audit: which user triggered this
    admin_action = Column(Boolean, default=False)  # Is this an admin action?
    
    # Exception details
    traceback = Column(Text, nullable=True)  # Full stack trace (for admins only)
    
    # Additional context
    metadata_json = Column(JSON, default={})


class Alert(Base):
    """System and user alerts - 30 day retention for unread alerts"""
    __tablename__ = "alerts"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))  # UUID
    
    # Alert classification
    alert_type = Column(String, nullable=False, index=True)  # rebalance_completed, regime_change, etc.
    severity = Column(String, nullable=False)  # 'info', 'warning', 'critical', 'emergency'
    message = Column(Text, nullable=False)
    
    # Assignment
    user_id = Column(String, nullable=True, index=True)  # NULL = system-wide alert
    
    # Status
    is_read = Column(Boolean, default=False, index=True)
    action_required = Column(Boolean, default=False)
    
    # Email sent?
    email_sent = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    read_at = Column(DateTime, nullable=True)
    
    # Additional context
    metadata_json = Column(JSON, default={})


class AlertPreference(Base):
    """Client notification preferences"""
    __tablename__ = "alert_preferences"
    
    id = Column(String, primary_key=True)  # UUID
    user_id = Column(String, nullable=False, index=True, unique=True)
    
    # Alert type preferences (opt-in/out)
    rebalance_completed = Column(Boolean, default=True)
    regime_change = Column(Boolean, default=True)
    emergency_stop = Column(Boolean, default=True)
    transfer_needed = Column(Boolean, default=True)
    drawdown_warning = Column(Boolean, default=True)
    health_check_failed = Column(Boolean, default=False)  # Mostly for admins
    api_error = Column(Boolean, default=False)  # Mostly for admins
    
    # Notification methods
    email_enabled = Column(Boolean, default=True)
    daily_digest_enabled = Column(Boolean, default=True)
    daily_digest_time = Column(String, default="08:00")  # HH:MM format, EST
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SystemStatus(Base):
    """Overall system health and status"""
    __tablename__ = "system_status"
    
    id = Column(String, primary_key=True, default="system")
    
    # Component health
    database_connection = Column(Boolean, default=True)
    snaptrade_api_available = Column(Boolean, default=True)
    market_data_available = Column(Boolean, default=True)
    benchmark_data_available = Column(Boolean, default=False)
    
    # Last update timestamps
    last_market_data_refresh = Column(DateTime, nullable=True)
    last_benchmark_refresh = Column(DateTime, nullable=True)
    last_health_check = Column(DateTime, nullable=True)
    last_rebalance = Column(DateTime, nullable=True)
    
    # Current regime
    current_crypto_regime = Column(String, nullable=True)
    current_equities_regime = Column(String, nullable=True)
    
    # Trading state
    emergency_stop_active = Column(Boolean, default=False)
    emergency_stop_reason = Column(String, nullable=True)
    emergency_stop_triggered_at = Column(DateTime, nullable=True)
    
    # Metrics
    total_users = Column(Integer, default=0)
    active_users = Column(Integer, default=0)
    total_aum = Column(Float, default=0.0)  # Total Assets Under Management
    
    # Last update
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PerformanceSession(Base):
    """
    Performance tracking session.
    Controls when portfolio performance data should be recorded.
    Performance measurement starts only when a session is active.
    """
    __tablename__ = "performance_sessions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, index=True)
    
    # Session state
    is_active = Column(Boolean, default=True, index=True)
    
    # Performance baseline - starts at $1.00 (corresponds to 0% on charts)
    baseline_value = Column(Float, default=1.0)  # Base value for return calculations
    
    # Session timing
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    stopped_at = Column(DateTime, nullable=True)
    last_snapshot_at = Column(DateTime, nullable=True)
    
    # Benchmark tracking
    benchmark_start_date = Column(DateTime, nullable=True)  # 30 days before portfolio start
    benchmark_ticker = Column(String, default="SPY")  # Default benchmark
    
    # Session metadata
    metadata_json = Column(JSON, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BenchmarkSnapshot(Base):
    """
    Historical benchmark data storage.
    Benchmark data is stored starting 30 days before portfolio performance start date.
    """
    __tablename__ = "benchmark_snapshots"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, nullable=False, index=True)  # Links to PerformanceSession
    
    # Benchmark data
    ticker = Column(String, nullable=False, default="SPY")  # e.g., SPY, QQQ
    value = Column(Float, nullable=False)  # Benchmark value/price
    
    # For return calculations
    return_pct = Column(Float, default=0.0)  # Return percentage from session start
    
    # Timestamp
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    # Data source metadata
    source = Column(String, default="twelve_data")  # twelve_data, alpha_vantage, etc.


# Add indexes for efficient querying
Index('idx_perf_session_user_active', PerformanceSession.user_id, PerformanceSession.is_active)
Index('idx_benchmark_session_date', BenchmarkSnapshot.session_id, BenchmarkSnapshot.recorded_at)


# ============================================================================
# Database Initialization
# ============================================================================

def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
