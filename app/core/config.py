"""
Configuration

Environment variables and application settings.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    """Application settings from environment variables"""
    
    # API Configuration
    API_TITLE: str = "Portfolio Management Trading System"
    API_VERSION: str = "1.0.0"
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/portfolio")
    
    # SnapTrade
    SNAPTRADE_CLIENT_ID: str = os.getenv("SNAPTRADE_CLIENT_ID", "")
    SNAPTRADE_CLIENT_SECRET: str = os.getenv("SNAPTRADE_CLIENT_SECRET", "")
    SNAPTRADE_SANDBOX: bool = os.getenv("SNAPTRADE_SANDBOX", "True").lower() == "true"
    
    # JWT
    JWT_SECRET: str = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # CORS
    CORS_ORIGINS: list = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,https://your-vercel-app.vercel.app"
    ).split(",")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    
    # Trading Configuration
    TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "False").lower() == "true"
    REBALANCE_FREQUENCY: str = os.getenv("REBALANCE_FREQUENCY", "daily")
    MIN_POSITION_SIZE: float = float(os.getenv("MIN_POSITION_SIZE", "100"))
    MAX_POSITION_SIZE: float = float(os.getenv("MAX_POSITION_SIZE", "50000"))
    
    # Risk Settings
    DEFAULT_RISK_PROFILE: str = os.getenv("DEFAULT_RISK_PROFILE", "moderate")
    MAX_PORTFOLIO_VOLATILITY: float = float(os.getenv("MAX_PORTFOLIO_VOLATILITY", "0.20"))
    MAX_SINGLE_POSITION_PCT: float = float(os.getenv("MAX_SINGLE_POSITION_PCT", "0.30"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
