"""
Alert Management

Helper functions for creating and managing alerts.
"""

from app.models.database import SessionLocal, Alert
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    """Alert types"""
    REGIME_CHANGE = "REGIME_CHANGE"
    REBALANCE_FAILED = "REBALANCE_FAILED"
    TRADE_FAILED = "TRADE_FAILED"
    SYSTEM_HEALTH = "SYSTEM_HEALTH"
    LOW_CASH = "LOW_CASH"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    DATA_ERROR = "DATA_ERROR"


class AlertSeverity(str, Enum):
    """Alert severity levels"""
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def create_alert(
    alert_type: AlertType,
    severity: AlertSeverity,
    message: str,
    user_id: int = None,
    action_required: bool = False
) -> Alert:
    """Create and store an alert"""
    
    db = SessionLocal()
    
    try:
        alert = Alert(
            alert_type=alert_type.value,
            severity=severity.value,
            message=message,
            user_id=user_id,
            action_required=action_required,
            created_at=datetime.utcnow()
        )
        db.add(alert)
        db.commit()
        
        logger.warning(f"Alert created: {alert_type} - {message}")
        return alert
    
    except Exception as e:
        logger.error(f"Failed to create alert: {str(e)}")
        db.rollback()
        raise
    
    finally:
        db.close()


def get_unread_alerts(user_id: int = None, limit: int = 10) -> list:
    """Get unread alerts"""
    
    db = SessionLocal()
    
    try:
        query = db.query(Alert).filter(Alert.read == False)
        
        if user_id:
            query = query.filter(
                (Alert.user_id == user_id) | (Alert.user_id == None)
            )
        
        alerts = query.order_by(
            Alert.created_at.desc()
        ).limit(limit).all()
        
        return alerts
    
    finally:
        db.close()


def mark_alert_read(alert_id: int):
    """Mark alert as read"""
    
    db = SessionLocal()
    
    try:
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        if alert:
            alert.read = True
            db.commit()
            logger.info(f"Alert {alert_id} marked as read")
    
    except Exception as e:
        logger.error(f"Failed to mark alert read: {str(e)}")
        db.rollback()
    
    finally:
        db.close()


def dismiss_alert(alert_id: int):
    """Dismiss alert (delete from active alerts)"""
    
    db = SessionLocal()
    
    try:
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        if alert:
            db.delete(alert)
            db.commit()
            logger.info(f"Alert {alert_id} dismissed")
    
    except Exception as e:
        logger.error(f"Failed to dismiss alert: {str(e)}")
        db.rollback()
    
    finally:
        db.close()
