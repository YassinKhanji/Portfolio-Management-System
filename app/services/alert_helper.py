"""
Alert Helper Utilities

Creates alerts in database and triggers appropriate email notifications.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.database import Alert, User, SessionLocal
from app.core.config import get_settings
from app.services.email_service import get_email_service

logger = logging.getLogger(__name__)
settings = get_settings()


async def create_alert_with_email(
    user_id: int,
    alert_type: str,
    severity: str,
    message: str,
    email_immediately: bool = True,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """
    Create an alert in database and optionally send email.
    
    Args:
        user_id: User ID to create alert for
        alert_type: Type of alert (rebalance_completed, regime_change, etc)
        severity: Severity level (info, warning, critical, emergency)
        message: Alert message text
        email_immediately: Send email now (critical/emergency) or wait for daily digest
        db: Database session (creates new if not provided)
        
    Returns:
        Created Alert object or None if failed
    """
    
    # Create database session if not provided
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False
    
    try:
        # Create alert in database
        alert = Alert(
            user_id=user_id,
            alert_type=alert_type,
            severity=severity,
            message=message,
            is_read=False,
            email_sent=False,
            created_at=datetime.now(timezone.utc)
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        
        logger.info("[OK] Alert created")
        
        # Send email immediately for critical/emergency alerts
        if email_immediately and severity in ["critical", "emergency"]:
            # Get user email in background
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                try:
                    email_service = get_email_service()
                    success = await email_service.send_email(
                        to_email=user.email,
                        subject=f"[{severity.upper()}] {alert_type.replace('_', ' ').title()}",
                        html_content=_get_alert_html(alert_type, severity, message)
                    )
                    
                    if success:
                        alert.email_sent = True
                        db.commit()
                        logger.info("[OK] Alert email sent")
                    else:
                        logger.warning("Failed to send alert email")
                        
                except Exception as e:
                    logger.error("Error sending alert email", exc_info=True)
        
        return alert
        
    except Exception as e:
        logger.error("Error creating alert", exc_info=True)
        db.rollback()
        return None
        
    finally:
        if should_close:
            db.close()


def _get_alert_html(alert_type: str, severity: str, message: str) -> str:
    """Generate HTML email content for alert"""
    
    severity_colors = {
        "info": "#1976d2",      # Blue
        "warning": "#f57c00",   # Orange
        "critical": "#d32f2f",  # Red
        "emergency": "#b71c1c"  # Dark Red
    }
    
    color = severity_colors.get(severity, "#333")
    
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="border-left: 5px solid {color}; padding: 20px; background: #f5f5f5; margin: 20px 0;">
            <h2 style="color: {color}; margin-top: 0;">
                {alert_type.replace('_', ' ').title()}
            </h2>
            <p style="font-size: 1.1em; margin: 15px 0;">
                {message}
            </p>
            <p style="font-size: 0.9em; color: #666; margin: 10px 0;">
                <strong>Severity:</strong> {severity.upper()}
            </p>
        </div>
        
        <hr style="margin: 30px 0;">
        <p style="font-size: 0.9em; color: #666;">
            This is an automated alert from Portfolio Management System.<br>
            Log into your dashboard to view full details and manage alert preferences.
        </p>
    </body>
    </html>
    """
    return html


async def create_rebalance_alert(
    user_id: int,
    trades_executed: int,
    portfolio_value: float,
    new_allocation: Dict[str, float],
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create rebalance completed alert"""
    message = f"Portfolio rebalanced: {trades_executed} trades executed"
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="rebalance_completed",
        severity="info",
        message=message,
        email_immediately=False,
        db=db
    )


async def create_regime_change_alert(
    user_id: int,
    old_regime: str,
    new_regime: str,
    confidence: float,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create market regime change alert"""
    message = f"Market regime changed from {old_regime} to {new_regime} (confidence: {confidence*100:.0f}%)"
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="regime_change",
        severity="warning",
        message=message,
        email_immediately=False,
        db=db
    )


async def create_emergency_alert(
    user_id: int,
    message: str,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create critical/emergency alert - sends email immediately"""
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="emergency_stop_triggered",
        severity="emergency",
        message=message,
        email_immediately=True,
        db=db
    )


async def create_drawdown_alert(
    user_id: int,
    portfolio_value: float,
    drawdown_percent: float,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create portfolio drawdown warning alert"""
    message = f"Portfolio drawdown: {drawdown_percent:.1f}% from recent highs (current value: ${portfolio_value:,.2f})"
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="drawdown_warning",
        severity="warning",
        message=message,
        email_immediately=False,
        db=db
    )


async def create_api_error_alert(
    user_id: int,
    api_name: str,
    error_message: str,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create API error alert for admins"""
    message = f"API Error in {api_name}: {error_message}"
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="api_error",
        severity="critical",
        message=message,
        email_immediately=True,
        db=db
    )


async def create_transfer_needed_alert(
    user_id: int,
    from_account: str,
    to_account: str,
    amount: float,
    db: Optional[Session] = None
) -> Optional[Alert]:
    """Create transfer recommendation alert"""
    message = f"Transfer ${amount:,.2f} from {from_account} to {to_account}"
    return await create_alert_with_email(
        user_id=user_id,
        alert_type="transfer_needed",
        severity="info",
        message=message,
        email_immediately=False,
        db=db
    )
