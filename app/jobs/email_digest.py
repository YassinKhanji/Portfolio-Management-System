"""
Email Digest Job

Sends weekly email summary to clients (Saturday 12:00 PM EST).
Includes: Portfolio snapshot, performance metrics, recent alerts, transfer recommendations.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from app.models.database import SessionLocal, User, PortfolioSnapshot, Alert, AlertPreference
from types import SimpleNamespace
from app.core.config import get_settings
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_weekly_digest():
    """
    Send weekly email digest to all clients who have enabled it.
    Called weekly (Saturday 12:00 PM EST) by APScheduler.
    """
    db = SessionLocal()
    try:
        # Get all active, onboarded users who want the weekly digest
        users_query = (
            db.query(User, AlertPreference)
            .outerjoin(AlertPreference, User.id == AlertPreference.user_id)
            .filter(
                User.active == True,
                User.role == "client",
            )
            .all()
        )
        
        sent_count = 0
        error_count = 0
        
        for user, alert_pref in users_query:
            try:
                # Check if user has weekly digest enabled
                if alert_pref and not alert_pref.daily_digest_enabled:
                    continue
                
                # Get latest portfolio snapshot
                latest_snapshot = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.user_id == user.id)
                    .order_by(PortfolioSnapshot.recorded_at.desc())
                    .first()
                )

                # Fallback snapshot so emails still send even if portfolio data is missing
                snapshot = latest_snapshot or SimpleNamespace(
                    total_value=0.0,
                    daily_return=0.0,
                    daily_return_pct=0.0,
                    crypto_value=0.0,
                    stocks_value=0.0,
                    cash_value=0.0,
                    recorded_at=datetime.now(timezone.utc),
                )
                
                # Get unread alerts for this user
                try:
                    unread_alerts = (
                        db.query(Alert)
                        .filter(
                            Alert.user_id == user.id,
                            Alert.is_read == False,
                            Alert.created_at >= datetime.now(timezone.utc) - timedelta(days=7)
                        )
                        .order_by(Alert.created_at.desc())
                        .limit(10)
                        .all()
                    )
                except Exception:
                    # Older schemas may not have is_read; fallback to no alerts
                    unread_alerts = []
                
                # Build email content
                subject = f"Weekly Portfolio Summary - {datetime.now(timezone.utc).strftime('%B %d, %Y')}"
                html_content = _build_digest_html(user, snapshot, unread_alerts)
                
                # Send email
                _send_email(
                    to_email=user.email,
                    subject=subject,
                    html_content=html_content
                )
                
                sent_count += 1
                
                # Mark alerts as email sent (but not as read)
                if unread_alerts:
                    for alert in unread_alerts:
                        alert.email_sent = True
                    db.commit()
                
            except Exception as e:
                logger.error("Failed to send digest", exc_info=True)
                error_count += 1
        
        logger.info(f"[OK] Weekly email digests sent: {sent_count} success, {error_count} errors")
        
    except Exception as e:
        logger.error("Failed to send weekly digests", exc_info=True)
    finally:
        db.close()


def _build_digest_html(user: User, snapshot: PortfolioSnapshot, alerts: list) -> str:
    """Build HTML email content for weekly digest"""
    
    alerts_html = ""
    if alerts:
        alerts_html = "<h3>Recent Alerts</h3><ul>"
        for alert in alerts:
            alerts_html += f"<li><strong>{alert.alert_type}</strong> ({alert.severity}): {alert.message}</li>"
        alerts_html += "</ul>"
    
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Weekly Portfolio Summary - {datetime.now().strftime('%B %d, %Y')}</h2>
        
        <h3>Portfolio Value</h3>
        <p>
            <strong>Total Value:</strong> ${snapshot.total_value:,.2f} CAD<br>
            <strong>Daily Change:</strong> ${snapshot.daily_return:,.2f} ({snapshot.daily_return_pct:+.2f}%)<br>
            <strong>Crypto:</strong> ${snapshot.crypto_value:,.2f}<br>
            <strong>Stocks:</strong> ${snapshot.stocks_value:,.2f}<br>
            <strong>Cash:</strong> ${snapshot.cash_value:,.2f}
        </p>
        
        <h3>Allocation</h3>
        <p>
            <strong>Target Allocation ({user.risk_profile}):</strong><br>
            Based on your risk profile, your portfolio should be allocated as follows.
            Check your dashboard for recommended transfers between accounts.
        </p>
        
        {alerts_html}
        
        <hr>
        <p>
            <a href="{settings.CORS_ORIGINS[0]}">View full dashboard</a>
        </p>
        <p style="font-size: 0.9em; color: #666;">
            This is an automated message from Portfolio Management System.<br>
            To adjust notification preferences, visit your account settings.
        </p>
    </body>
    </html>
    """
    
    return html


def _send_email(to_email: str, subject: str, html_content: str) -> bool:
    """
    Send email via Gmail SMTP.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        from_name = getattr(settings, "SMTP_FROM_NAME", None) or "Portfolio Management System"
        msg["From"] = formataddr((from_name, settings.SMTP_FROM_EMAIL))
        msg["To"] = to_email
        
        # Attach HTML content
        html_part = MIMEText(html_content, "html")
        msg.attach(html_part)
        
        # Send via Gmail SMTP
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info("[OK] Email sent")
        return True
        
    except Exception as e:
        logger.error("Failed to send email", exc_info=True)
        return False
