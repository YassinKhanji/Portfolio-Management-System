"""
Email Digest Job

Sends daily email summary to clients (8:00 AM EST).
Includes: Portfolio snapshot, performance metrics, recent alerts, transfer recommendations.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.models.database import SessionLocal, User, PortfolioSnapshot, Alert, AlertPreference
from app.core.config import get_settings
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_daily_digest():
    """
    Send daily email digest to all clients who have enabled it.
    Called daily at 8:00 AM EST by APScheduler.
    """
    db = SessionLocal()
    try:
        # Get all active, onboarded users who want daily digest
        users_query = (
            db.query(User, AlertPreference)
            .outerjoin(AlertPreference, User.id == AlertPreference.user_id)
            .filter(
                User.is_active == True,
                User.is_onboarded == True,
                User.role == "client",
            )
            .all()
        )
        
        sent_count = 0
        error_count = 0
        
        for user, alert_pref in users_query:
            try:
                # Check if user has daily digest enabled
                if alert_pref and not alert_pref.daily_digest_enabled:
                    continue
                
                # Get latest portfolio snapshot
                latest_snapshot = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.user_id == user.id)
                    .order_by(PortfolioSnapshot.recorded_at.desc())
                    .first()
                )
                
                if not latest_snapshot:
                    continue  # Skip if no portfolio data
                
                # Get unread alerts for this user
                unread_alerts = (
                    db.query(Alert)
                    .filter(
                        Alert.user_id == user.id,
                        Alert.is_read == False,
                        Alert.created_at >= datetime.utcnow() - timedelta(days=1)
                    )
                    .order_by(Alert.created_at.desc())
                    .limit(10)
                    .all()
                )
                
                # Build email content
                subject = f"Portfolio Summary - {datetime.now().strftime('%B %d, %Y')}"
                html_content = _build_digest_html(user, latest_snapshot, unread_alerts)
                
                # Send email
                _send_email(
                    to_email=user.email,
                    subject=subject,
                    html_content=html_content
                )
                
                sent_count += 1
                
                # Mark alerts as email sent (but not as read)
                for alert in unread_alerts:
                    alert.email_sent = True
                db.commit()
                
            except Exception as e:
                logger.error(f"Failed to send digest to user {user.email}: {str(e)}", exc_info=True)
                error_count += 1
        
        logger.info(f"[OK] Daily email digests sent: {sent_count} success, {error_count} errors")
        
    except Exception as e:
        logger.error(f"Failed to send daily digests: {str(e)}", exc_info=True)
    finally:
        db.close()


def _build_digest_html(user: User, snapshot: PortfolioSnapshot, alerts: list) -> str:
    """Build HTML email content for daily digest"""
    
    alerts_html = ""
    if alerts:
        alerts_html = "<h3>Recent Alerts</h3><ul>"
        for alert in alerts:
            alerts_html += f"<li><strong>{alert.alert_type}</strong> ({alert.severity}): {alert.message}</li>"
        alerts_html += "</ul>"
    
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Portfolio Summary - {datetime.now().strftime('%B %d, %Y')}</h2>
        
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
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = to_email
        
        # Attach HTML content
        html_part = MIMEText(html_content, "html")
        msg.attach(html_part)
        
        # Send via Gmail SMTP
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"[OK] Email sent to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}", exc_info=True)
        return False
