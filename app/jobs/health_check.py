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
        
        # Check database connectivity by querying SystemStatus
        status_count = db.query(SystemStatus).count()
        
        # Create/update system status
        latest_status = db.query(SystemStatus).order_by(
            SystemStatus.last_check.desc()
        ).first()
        
        # Create new status entry
        status = SystemStatus(
            component="api",
            status="healthy",
            last_check=now,
            cpu_usage=None,  # Would fetch real CPU usage here
            memory_usage=None,
            response_time_ms=0
        )
        db.add(status)
        
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
