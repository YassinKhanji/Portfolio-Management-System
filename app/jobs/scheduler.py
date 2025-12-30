"""
Scheduled Jobs Configuration

APScheduler setup for background tasks.
Based on SYSTEM_SPECIFICATIONS.md
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.jobs import data_refresh, daily_rebalance, health_check, portfolio_snapshot, email_digest, holdings_sync
import asyncio
from app.jobs.utils import is_emergency_stop_active
from app.core.config import get_settings
import logging

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
settings = get_settings()


def add_jobs():
    """Add all scheduled jobs"""
    
    # ========================================================================
    # 1. MARKET DATA REFRESH: Every 4 hours
    # ========================================================================
    scheduler.add_job(
        data_refresh.refresh_market_data,
        CronTrigger(hour="*/4"),
        id="refresh_market_data",
        name="Refresh Market Data",
        replace_existing=True
    )
    logger.info("[OK] Added job: refresh_market_data (every 4 hours)")
    
    # ========================================================================
    # 2. PORTFOLIO REBALANCING: 3 times per week (Sunday, Tuesday, Friday)
    # Starting Sunday 00:00 AM EST
    # Schedule:
    #   - Sunday 00:00 AM EST
    #   - Tuesday 16:00 (4:00 PM) EST
    #   - Friday 08:00 (8:00 AM) EST
    # ========================================================================
    
    # Sunday 00:00 AM EST
    scheduler.add_job(
        daily_rebalance.rebalance_portfolios,
        CronTrigger(day_of_week="sun", hour=5, minute=0),  # 00:00 EST = 05:00 UTC
        id="rebalance_sunday",
        name="Rebalance Portfolio (Sunday 00:00 EST)",
        replace_existing=True
    )
    logger.info("[OK] Added job: rebalance_portfolios (Sunday 00:00 EST)")
    
    # Tuesday 16:00 (4:00 PM) EST
    scheduler.add_job(
        daily_rebalance.rebalance_portfolios,
        CronTrigger(day_of_week="tue", hour=21, minute=0),  # 16:00 EST = 21:00 UTC
        id="rebalance_tuesday",
        name="Rebalance Portfolio (Tuesday 16:00 EST)",
        replace_existing=True
    )
    logger.info("[OK] Added job: rebalance_portfolios (Tuesday 16:00 EST)")
    
    # Friday 08:00 (8:00 AM) EST
    scheduler.add_job(
        daily_rebalance.rebalance_portfolios,
        CronTrigger(day_of_week="fri", hour=13, minute=0),  # 08:00 EST = 13:00 UTC
        id="rebalance_friday",
        name="Rebalance Portfolio (Friday 08:00 EST)",
        replace_existing=True
    )
    logger.info("[OK] Added job: rebalance_portfolios (Friday 08:00 EST)")
    
    # ========================================================================
    # 3. HOLDINGS SYNC: Every hour (runs at :50 past the hour)
    # Syncs SnapTrade holdings to the Position table for AUM tracking
    # This is critical for accurate portfolio values and performance tracking
    # ========================================================================
    scheduler.add_job(
        holdings_sync.sync_all_holdings,
        CronTrigger(minute=50),  # Run at :50 past every hour
        id="sync_holdings",
        name="Sync SnapTrade Holdings",
        replace_existing=True
    )
    logger.info("[OK] Added job: sync_holdings (every hour at :50)")
    
    # ========================================================================
    # 3b. TRANSACTION SYNC: Every hour (runs at :55 past the hour)
    # Syncs SnapTrade orders to the Transaction table for transaction history
    # Runs 5 minutes after holdings sync to ensure positions are up to date
    # ========================================================================
    scheduler.add_job(
        holdings_sync.sync_all_transactions,
        CronTrigger(minute=55),  # Run at :55 past every hour
        id="sync_transactions",
        name="Sync SnapTrade Transactions",
        replace_existing=True
    )
    logger.info("[OK] Added job: sync_transactions (every hour at :55)")
    
    # ========================================================================
    # 4. PORTFOLIO SNAPSHOTS: Every 4 hours
    # Used for performance charts and historical tracking
    # ========================================================================
    scheduler.add_job(
        portfolio_snapshot.create_snapshots,
        CronTrigger(hour="*/4"),
        id="portfolio_snapshot",
        name="Create Portfolio Snapshots",
        replace_existing=True
    )
    logger.info("[OK] Added job: portfolio_snapshot (every 4 hours)")
    
    # ========================================================================
    # 5. SYSTEM HEALTH CHECK: Every hour
    # ========================================================================
    scheduler.add_job(
        health_check.check_system_health,
        CronTrigger(hour="*"),
        id="health_check",
        name="System Health Check",
        replace_existing=True
    )
    logger.info("[OK] Added job: health_check (every hour)")
    
    # ========================================================================
    # 5. WEEKLY EMAIL DIGEST: Saturday at 12:00 PM EST
    # Sends portfolio summary + alerts to clients
    # ========================================================================
    async def _run_weekly_digest():
        await email_digest.send_weekly_digest()

    scheduler.add_job(
        lambda: asyncio.run(_run_weekly_digest()),
        CronTrigger(day_of_week="sat", hour=17, minute=0),  # 12:00 PM EST = 17:00 UTC
        id="weekly_email_digest",
        name="Send Weekly Email Digest",
        replace_existing=True
    )
    logger.info("[OK] Added job: weekly_email_digest (Saturday 12:00 PM EST)")


def start_scheduler():
    """Start background scheduler"""
    if is_emergency_stop_active():
        logger.warning("Scheduler not started because emergency_stop is active")
        return

    if not scheduler.running:
        add_jobs()
        scheduler.start()
        logger.info("[OK] Background scheduler started")


def stop_scheduler():
    """Stop background scheduler"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[OK] Background scheduler stopped")


def stop_jobs_for_emergency(reason: str = ""):
    """Stop scheduler immediately when an emergency stop is triggered."""
    if scheduler.running:
        logger.warning("Stopping scheduler due to emergency stop%s", f": {reason}" if reason else "")
        scheduler.shutdown(wait=False)
