"""
Main FastAPI Application

Entry point for the Portfolio Management Trading System API.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.security import limiter, add_security_headers
from app.models.database import init_db
try:
    from app.jobs.scheduler import start_scheduler, stop_scheduler
except Exception as e:
    start_scheduler = None  # type: ignore
    stop_scheduler = None   # type: ignore
    import warnings
    warnings.warn(f"Scheduler import failed; background jobs disabled: {e}")
from app.routers import rebalancing_router, system_router, portfolio_router, auth_router, admin_router

# Setup logging
logger = setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    
    # Startup
    logger.info("Starting Portfolio Management Trading System...")
    init_db()
    if start_scheduler is not None:
        try:
            start_scheduler()
        except Exception as se:
            logger.warning(f"Scheduler failed to start: {se}")
    else:
        logger.info("Scheduler disabled; skipping start")
    
    # Trigger initial holdings sync on startup
    try:
        from app.jobs.holdings_sync import sync_all_holdings_sync
        logger.info("Running initial holdings sync...")
        result = sync_all_holdings_sync()
        logger.info(f"Initial sync complete: {result.get('users_processed', 0)} users, ${result.get('total_aum', 0):,.2f} AUM")
    except Exception as sync_err:
        logger.warning(f"Initial holdings sync failed: {sync_err}")
    
    logger.info("[OK] System initialized and ready")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    if stop_scheduler is not None:
        try:
            stop_scheduler()
        except Exception as se:
            logger.warning(f"Scheduler failed to stop: {se}")
    logger.info("[OK] System shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    lifespan=lifespan
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(rebalancing_router)
app.include_router(system_router)
app.include_router(portfolio_router)
app.include_router(auth_router)
app.include_router(admin_router)


# Health check endpoint
@app.get("/health")
def health_check():
    """System health check endpoint"""
    return {
        "status": "healthy",
        "service": "Portfolio Management Trading System",
        "version": settings.API_VERSION
    }


# Root endpoint
@app.get("/")
def root():
    """Root endpoint with API information"""
    return {
        "name": settings.API_TITLE,
        "version": settings.API_VERSION,
        "docs": "/docs",
        "status": "running"
    }


# Error handlers
@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return {
        "error": "Internal server error",
        "detail": str(exc) if settings.DEBUG else "An error occurred"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
