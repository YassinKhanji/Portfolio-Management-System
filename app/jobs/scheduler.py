"""
Scheduled Jobs Configuration

APScheduler setup for background tasks.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.jobs import data_refresh, daily_rebalance, health_check
import logging

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def add_jobs():
    """Add all scheduled jobs"""
    
    # Market data refresh: Every 4 hours
    scheduler.add_job(
        data_refresh.refresh_market_data,
        CronTrigger(hour="*/4"),
        id="refresh_market_data",
        name="Refresh Market Data",
        replace_existing=True
    )
    logger.info("✓ Added job: refresh_market_data")
    
    # Portfolio rebalance: Daily at 9:00 AM EST
    scheduler.add_job(
        daily_rebalance.rebalance_portfolios,
        CronTrigger(hour=14, minute=0, day_of_week="mon-fri"),  # 9 AM EST = 2 PM UTC
        id="daily_rebalance",
        name="Daily Rebalance",
        replace_existing=True
    )
    logger.info("✓ Added job: daily_rebalance")
    
    # System health check: Every hour
    scheduler.add_job(
        health_check.check_system_health,
        CronTrigger(hour="*"),
        id="health_check",
        name="System Health Check",
        replace_existing=True
    )
    logger.info("✓ Added job: health_check")


def start_scheduler():
    """Start background scheduler"""
    if not scheduler.running:
        add_jobs()
        scheduler.start()
        logger.info("Background scheduler started")


def stop_scheduler():
    """Stop background scheduler"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Background scheduler stopped")
