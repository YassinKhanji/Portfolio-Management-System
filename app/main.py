"""
Main FastAPI Application

Entry point for the Portfolio Management Trading System API.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.models.database import init_db
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routers import rebalancing_router, system_router, portfolio_router

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
    start_scheduler()
    logger.info("✓ System initialized and ready")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    stop_scheduler()
    logger.info("✓ System shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    lifespan=lifespan
)

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
