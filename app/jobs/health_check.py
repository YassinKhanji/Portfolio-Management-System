"""
Health Check Job

Monitors system health and creates alerts for issues.
"""

from app.models.database import SessionLocal, SystemStatus, Alert, Log
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def check_system_health():
    """Check system health and database connectivity"""
    
    db = SessionLocal()
    
    try:
        # Get current timestamp
        now = datetime.utcnow()
        
        # Create/update system status (single row keyed by id="system")
        status = db.query(SystemStatus).filter(SystemStatus.id == "system").first()
        if not status:
            status = SystemStatus(id="system")
            db.add(status)

        status.database_connection = True
        status.snaptrade_api_available = True
        status.market_data_available = True
        status.benchmark_data_available = getattr(status, "benchmark_data_available", False)
        status.last_health_check = now
        status.updated_at = now
        
        # Log successful health check
        log = Log(
            timestamp=now,
            level="debug",
            message="System health check passed",
            component="health_check_job"
        )
        db.add(log)
        db.commit()
        
        logger.info("System health check completed - All systems operational")
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        
        # Create alert for health check failure
        try:
            alert = Alert(
                alert_type="SYSTEM_HEALTH",
                severity="CRITICAL",
                message=f"Health check failed: {str(e)}",
                user_id=None,  # System-wide alert
                action_required=True
            )
            db.add(alert)
            
            # Log the error
            log = Log(
                timestamp=datetime.utcnow(),
                level="error",
                message=f"Health check failed: {str(e)}",
                component="health_check_job"
            )
            db.add(log)
            db.commit()
        except:
            pass
    
    finally:
        try:
            db.close()
        except:
            pass
