"""
System Status & Monitoring Endpoints

Endpoints:
- GET /api/regime/status - Current market regime
- GET /api/system/health - System health check
- GET /api/system/logs - Recent logs
- GET /api/system/alerts - Recent alerts
- POST /api/system/emergency-stop - Emergency stop
- POST /api/system/emergency-stop/reset - Resume trading
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pathlib import Path
import itertools
from sqlalchemy.orm import Session
import logging
from datetime import datetime

from ..models.database import SessionLocal, User, Connection, Position, Log, SystemStatus as SystemStatusModel
from ..jobs.scheduler import start_scheduler, stop_jobs_for_emergency

router = APIRouter(prefix="/api", tags=["system"])
logger = logging.getLogger(__name__)


# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/regime/status")
async def get_regime_status():
    """
    Get current market regime status from both crypto and equities systems.
    
    Returns:
        {
            "crypto": {
                "season": "BULL|BEAR|SIDEWAYS|HODL",
                "vol_regime": 0|1|2,
                "dir_regime": 0|1|2,
                "confidence": 0.85,
                "btc_season": "BULL",
                "timestamp": "2024-01-15T10:00:00Z"
            },
            "equities": {
                "regime": "BULL|BEAR|CORRECTION|FLIGHT_TO_QUALITY",
                "confidence": 0.75,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        }
    """
    try:
        logger.info("Regime status requested")
        
        # TODO: wire real regime detectors; returning stubbed values for now
        response = {
            "crypto": {
                "season": "BULL",
                "vol_regime": 2,
                "dir_regime": 1,
                "confidence": 0.85,
                "btc_season": "BULL",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            },
            "equities": {
                "regime": "BULL",
                "confidence": 0.75,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            },
            "combined_signal": "RISK_ON",  # Derived from both
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }
        
        logger.info(f"Regime status: crypto={response['crypto']['season']}, equities={response['equities']['regime']}")
        return response
        
    except Exception as e:
        logger.error(f"Failed to get regime status: {str(e)}", exc_info=True)
        # Return fallback data instead of error
        return {
            "crypto": {
                "season": "UNKNOWN",
                "vol_regime": 1,
                "dir_regime": 1,
                "confidence": 0.0,
                "btc_season": "UNKNOWN",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            },
            "equities": {
                "regime": "UNKNOWN",
                "confidence": 0.0,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            },
            "combined_signal": "UNKNOWN",
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "error": str(e)
        }


@router.get("/system/health")
async def get_system_health(db: Session = Depends(get_db)):
    """
    Get system health status with real database queries.
    
    Returns:
        {
            "status": "healthy|degraded|critical",
            "regime_engine": true,
            "database_connection": true,
            "market_data_age_minutes": 15,
            "total_users": 42,
            "active_users": 35,
            "total_aum": 5234567.89,
            "emergency_stop": false,
            "last_rebalance": "2024-01-15T09:30:00Z"
        }
    """
    try:
        logger.info("System health check requested")
        
        # Check database connection
        database_connected = True
        try:
            user_count = db.query(User).count()
        except Exception as db_error:
            logger.error(f"Database connection failed: {str(db_error)}")
            database_connected = False
            user_count = 0
        
        # Regime engine not wired yet (crypto-only detector stubbed)
        regime_engine_running = False
        
        # Active users (no last_login tracking yet)
        active_users = user_count

        # Total AUM placeholder (not yet calculated)
        total_aum = None

        # Pull current emergency stop state
        system_status = db.query(SystemStatusModel).filter(SystemStatusModel.id == "system").first()
        emergency_stop = bool(system_status and system_status.emergency_stop_active)
        market_data_age_minutes = None

        status = "healthy" if database_connected else "critical"
        
        health_data = {
            "status": status,
            "regime_engine": regime_engine_running,
            "database_connection": database_connected,
            "market_data_age_minutes": market_data_age_minutes,
            "total_users": user_count,
            "active_users": active_users,
            "total_aum": total_aum,
            "emergency_stop": emergency_stop,
            "last_rebalance": None,  # TODO: Track last rebalance time
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        logger.info(f"System health: {status}")
        return health_data
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        # Return degraded status on error
        return {
            "status": "critical",
            "regime_engine": False,
            "database_connection": False,
            "market_data_age_minutes": 999,
            "total_users": 0,
            "active_users": 0,
            "total_aum": 0.0,
            "emergency_stop": False,
            "last_rebalance": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": str(e)
        }


@router.get("/system/logs")
async def get_system_logs(
    level: Optional[str] = None,
    component: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100
):
    """
    Get recent system logs from rotating log files.

    Args:
        level: Filter by log level (debug, info, warning, error, critical)
        component: Filter by logger name/component substring
        q: Full-text search on message
        limit: Maximum number of log lines (cap at 500)

    Returns:
        List of log entries sorted newest-first
    """
    try:
        max_limit = 500
        limit = max(1, min(limit, max_limit))

        log_dir = Path("logs")
        log_files = [
            log_dir / "app.log",
            log_dir / "error.log",
            log_dir / "jobs.log",
        ]

        entries: List[dict] = []

        def parse_line(line: str):
            """Parse log line from formatter: %(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"""
            try:
                parts = line.strip().split(" - ", 4)
                if len(parts) < 5:
                    return None
                ts_raw, logger_name, levelname, location, message = parts
                # Convert timestamp to ISO-like string
                ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f")
                return {
                    "timestamp": ts.isoformat() + "Z",
                    "level": levelname.lower(),
                    "component": logger_name,
                    "location": location.strip("[]"),
                    "message": message,
                }
            except Exception:
                return None

        for file_path in log_files:
            if not file_path.exists():
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception as fe:
                logger.warning(f"Failed reading log file {file_path}: {fe}")
                continue
            for line in lines[-1000:]:  # read last 1000 lines per file to stay bounded
                parsed = parse_line(line)
                if not parsed:
                    continue
                entries.append(parsed)

        # Apply filters
        if level:
            level_lower = level.lower()
            entries = [e for e in entries if e["level"] == level_lower]
        if component:
            comp_lower = component.lower()
            entries = [e for e in entries if comp_lower in e.get("component", "").lower()]
        if q:
            q_lower = q.lower()
            entries = [e for e in entries if q_lower in e.get("message", "").lower()]

        # Sort newest first by timestamp
        entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return entries[:limit]

    except Exception as e:
        logger.error(f"Failed to fetch logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch logs")


@router.get("/system/alerts")
async def get_system_alerts(
    unread_only: bool = False,
    limit: int = 50
) -> List[dict]:
    """
    Get recent alerts.
    
    Args:
        unread_only: Only unread alerts
        limit: Max alerts to return
        
    Returns:
        List of alert messages
    """
    try:
        logger.info(f"Alerts requested (unread_only={unread_only}, limit={limit})")
        
        # TODO: Query alerts from database
        
        return [
            {
                "id": "alert_123",
                "type": "regime_change",
                "severity": "info",
                "message": "Regime changed from BULL to SIDEWAYS",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "read": False
            }
        ]
    except Exception as e:
        logger.error(f"Failed to get alerts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/emergency-stop")
async def emergency_stop(reason: str = "Manual admin stop", db: Session = Depends(get_db)):
    """
    Emergency stop all trading (admin only).
    
    Args:
        reason: Reason for emergency stop
        
    Returns:
        {"status": "stopped", "reason": "...", "timestamp": "..."}
    """
    try:
        logger.critical(f"EMERGENCY STOP triggered: {reason}")
        
        # Update system status in database
        system_status = db.query(SystemStatusModel).filter(SystemStatusModel.id == "system").first()
        if not system_status:
            system_status = SystemStatusModel(id="system")

        now = datetime.utcnow()
        system_status.emergency_stop_active = True
        system_status.emergency_stop_reason = reason
        system_status.emergency_stop_triggered_at = now
        system_status.updated_at = now
        
        db.add(system_status)
        db.commit()
        stop_jobs_for_emergency(reason)
        
        logger.warning(f"Emergency stop activated: {reason}")
        
        return {
            "status": "stopped",
            "reason": reason,
            "emergency_stop": True,
            "timestamp": now.isoformat() + "Z"
        }
    except Exception as e:
        logger.error(f"Emergency stop failed: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/emergency-stop/reset")
async def emergency_stop_reset(db: Session = Depends(get_db)):
    """
    Reset emergency stop and resume trading (admin only).
    
    Returns:
        {"status": "resumed", "timestamp": "..."}
    """
    try:
        logger.info("Emergency stop RESET - trading resumed")
        
        # Clear emergency stop flag in database
        system_status = db.query(SystemStatusModel).filter(SystemStatusModel.id == "system").first()
        if not system_status:
            system_status = SystemStatusModel(id="system")

        now = datetime.utcnow()
        system_status.emergency_stop_active = False
        system_status.emergency_stop_reason = None
        system_status.emergency_stop_triggered_at = None
        system_status.updated_at = now
        
        db.add(system_status)
        db.commit()
        start_scheduler()
        
        logger.warning("Emergency stop has been RESET - trading resumed")
        
        return {
            "status": "resumed",
            "emergency_stop": False,
            "timestamp": now.isoformat() + "Z"
        }
    except Exception as e:
        logger.error(f"Emergency stop reset failed: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
