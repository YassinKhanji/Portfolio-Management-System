"""
Database Models

SQLAlchemy ORM models for the shared database between Trading System and Web App.

Tables:
- Users (user profiles, SnapTrade tokens, risk profiles)
- Positions (current holdings)
- Transactions (trade history)
- Regimes (market regime states)
- Logs (system logs)
- Alerts (system alerts and notifications)
- SystemStatus (overall system health)
"""

from sqlalchemy import create_engine, Column, String, Float, DateTime, Boolean, Integer, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from typing import Optional
import os

# Database URL (set in environment)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost/portfolio_management"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ============================================================================
# Models
# ============================================================================

class User(Base):
    """User account and preferences"""
    __tablename__ = "users"
    
    id = Column(String, primary_key=True)  # UUID or SnapTrade user ID
    email = Column(String, unique=True, nullable=False)
    snaptrade_token = Column(String, nullable=False)  # Encrypted
    snaptrade_user_id = Column(String, nullable=False)
    
    # User preferences
    risk_profile = Column(String, default="Balanced")  # Conservative, Balanced, Aggressive
    rebalance_frequency = Column(String, default="weekly")  # daily, weekly, monthly
    
    # Account info
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_json = Column(JSON, default={})  # Custom user settings


class Position(Base):
    """Current portfolio holdings"""
    __tablename__ = "positions"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)  # Foreign key to users
    symbol = Column(String, nullable=False)  # e.g., "BTC.X", "ETH.X"
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # Current price
    market_value = Column(Float, nullable=False)  # quantity * price
    
    # Allocation tracking
    target_percentage = Column(Float, nullable=False)
    allocation_percentage = Column(Float, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_json = Column(JSON, default={})


class Transaction(Base):
    """Trade execution history"""
    __tablename__ = "transactions"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)  # Foreign key to users
    
    # Trade details
    symbol = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    side = Column(String, nullable=False)  # BUY or SELL
    
    # Status tracking
    status = Column(String, default="pending")  # pending, executed, failed
    snaptrade_order_id = Column(String, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
    metadata_json = Column(JSON, default={})


class Regime(Base):
    """Market regime history"""
    __tablename__ = "regimes"
    
    id = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Regime detection
    season = Column(String, nullable=False)  # BULL, BEAR, SIDEWAYS, HODL
    vol_regime = Column(Integer, nullable=False)  # 0, 1, 2
    dir_regime = Column(Integer, nullable=False)  # 0, 1, 2
    confidence = Column(Float, default=0.8)
    
    metadata_json = Column(JSON, default={})  # BTC, ETH details


class Log(Base):
    """System audit trail"""
    __tablename__ = "logs"
    
    id = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    level = Column(String, nullable=False)  # debug, info, warning, error, critical
    message = Column(Text, nullable=False)
    component = Column(String, nullable=False)  # Which system component
    user_id = Column(String, nullable=True)  # Optional: which user
    metadata_json = Column(JSON, default={})


class Alert(Base):
    """System and user alerts"""
    __tablename__ = "alerts"
    
    id = Column(String, primary_key=True)
    
    # Alert metadata
    alert_type = Column(String, nullable=False)  # rebalance_needed, trade_failed, regime_change, drift_alert, data_refresh_failed
    severity = Column(String, nullable=False)  # info, warning, critical
    message = Column(Text, nullable=False)
    
    # Assignment
    user_id = Column(String, nullable=True)  # null = system-wide alert
    read = Column(Boolean, default=False)
    action_required = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_json = Column(JSON, default={})


class SystemStatus(Base):
    """Overall system health and status"""
    __tablename__ = "system_status"
    
    id = Column(String, primary_key=True, default="system")
    
    # Component status
    regime_engine = Column(Boolean, default=True)
    database_connection = Column(Boolean, default=True)
    
    # Market data
    market_data_age_minutes = Column(Integer, default=0)
    market_data_last_updated = Column(DateTime, nullable=True)
    
    # User metrics
    total_users = Column(Integer, default=0)
    active_users = Column(Integer, default=0)
    total_aum = Column(Float, default=0.0)  # Total Assets Under Management
    
    # Trading status
    emergency_stop = Column(Boolean, default=False)
    last_rebalance = Column(DateTime, nullable=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_json = Column(JSON, default={})


class WebAppSession(Base):
    """Frontend user sessions"""
    __tablename__ = "web_app_sessions"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    token = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)


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
