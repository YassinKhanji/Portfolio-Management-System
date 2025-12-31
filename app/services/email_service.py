"""
Email Service

Handles all email notifications via Gmail SMTP.
Async implementation for efficient sending.
"""

import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import get_settings
from datetime import datetime, timezone
import logging
from typing import List, Dict

from app.models.database import SessionLocal, Log, User

logger = logging.getLogger(__name__)
settings = get_settings()


class EmailService:
    """Email service for sending notifications via Gmail SMTP"""

    def __init__(self):
        self.smtp_server = settings.SMTP_SERVER
        self.smtp_port = settings.SMTP_PORT
        self.username = settings.SMTP_USERNAME
        self.password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL
        self.from_name = settings.SMTP_FROM_NAME

    def _get_admin_recipients(self) -> List[str]:
        """Fetch active admin emails from the database"""
        db = SessionLocal()
        try:
            admins = db.query(User).filter(User.role == "admin", User.active == True).all()
            return [admin.email for admin in admins if admin.email]
        except Exception as e:
            logger.warning("Failed to fetch admin recipients: %s", e)
            return []
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _log_email_event(
        self,
        to_email: str,
        subject: str,
        success: bool,
        component: str = "email_service",
        reason: str = None,
        metadata: Dict = None
    ) -> None:
        """Persist email send attempts to the logs table"""
        db = SessionLocal()
        try:
            log = Log(
                timestamp=datetime.now(timezone.utc),
                level="info" if success else "error",
                message=f"Email {'sent' if success else 'failed'}",
                component=component,
                metadata_json={
                    "success": success,
                    "reason": reason,
                    **(metadata or {})
                }
            )
            db.add(log)
            db.commit()
        except Exception as log_error:
            logger.warning("Failed to write email log: %s", log_error)
        finally:
            try:
                db.close()
            except Exception:
                pass

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        plain_text: str = None
    ) -> bool:
        """
        Send email asynchronously.
        
        Args:
            to_email: Recipient email
            subject: Email subject
            html_content: HTML email body
            plain_text: Plain text fallback
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Run blocking SMTP operation in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._send_smtp,
                to_email,
                subject,
                html_content,
                plain_text
            )
            self._log_email_event(
                to_email=to_email,
                subject=subject,
                success=result,
                metadata={"has_plain_text": bool(plain_text)}
            )
            return result
        except Exception as e:
            logger.error("Failed to send email", exc_info=True)
            self._log_email_event(
                to_email=to_email,
                subject=subject,
                success=False,
                reason=str(e),
                metadata={"has_plain_text": bool(plain_text)}
            )
            return False

    def _send_smtp(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        plain_text: str = None
    ) -> bool:
        """Blocking SMTP send operation"""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

            # Attach plain text (if provided) and HTML
            if plain_text:
                text_part = MIMEText(plain_text, "plain")
                msg.attach(text_part)

            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)

            # Send via Gmail SMTP
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)

            logger.info("[OK] Email sent")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP Authentication failed. Check email credentials.")
            return False
        except smtplib.SMTPException as e:
            logger.error("SMTP error", exc_info=True)
            return False
        except Exception as e:
            logger.error("Error sending email", exc_info=True)
            return False

    async def send_rebalance_confirmation(
        self,
        user_email: str,
        user_name: str,
        trades_executed: int,
        portfolio_value: float,
        new_allocation: Dict[str, float]
    ) -> bool:
        """Send rebalance completion email"""
        subject = "Portfolio Rebalance Completed"
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Portfolio Rebalance Completed</h2>
            <p>Hello {user_name},</p>
            
            <p>Your portfolio rebalancing has been completed successfully.</p>
            
            <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>Trades Executed:</strong></td>
                    <td style="padding: 10px;">{trades_executed}</td>
                </tr>
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>Portfolio Value:</strong></td>
                    <td style="padding: 10px;">${portfolio_value:,.2f} CAD</td>
                </tr>
            </table>
            
            <h3>New Allocation:</h3>
            <ul>
                <li><strong>Crypto:</strong> {new_allocation.get('crypto', 0)*100:.1f}%</li>
                <li><strong>Stocks:</strong> {new_allocation.get('stocks', 0)*100:.1f}%</li>
                <li><strong>Cash:</strong> {new_allocation.get('cash', 0)*100:.1f}%</li>
            </ul>
            
            <hr style="margin: 30px 0;">
            <p style="font-size: 0.9em; color: #666;">
                This is an automated message from Portfolio Management System.<br>
                Questions? Log into your dashboard to view detailed transaction history.
            </p>
        </body>
        </html>
        """
        recipients = list(dict.fromkeys([user_email] + self._get_admin_recipients()))
        overall_success = True
        for recipient in recipients:
            sent = await self.send_email(recipient, subject, html_content)
            overall_success = overall_success and sent

        self._log_email_event(
            to_email=", ".join(recipients),
            subject=subject,
            success=overall_success,
            component="rebalance_notification",
            metadata={
                "trades_executed": trades_executed,
                "portfolio_value": portfolio_value,
                "recipient_count": len(recipients)
            }
        )

        return overall_success

    async def send_regime_change_alert(
        self,
        user_email: str,
        user_name: str,
        old_regime: str,
        new_regime: str,
        confidence: float
    ) -> bool:
        """Send market regime change alert"""
        subject = f"Market Regime Changed: {old_regime} → {new_regime}"
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Market Regime Changed</h2>
            <p>Hello {user_name},</p>
            
            <p>The market regime has shifted. This may affect your portfolio allocation.</p>
            
            <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>Previous Regime:</strong></td>
                    <td style="padding: 10px;">{old_regime}</td>
                </tr>
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>New Regime:</strong></td>
                    <td style="padding: 10px;">{new_regime}</td>
                </tr>
                <tr>
                    <td style="padding: 10px;"><strong>Confidence:</strong></td>
                    <td style="padding: 10px;">{confidence*100:.0f}%</td>
                </tr>
            </table>
            
            <p>Your next rebalancing is scheduled based on the regular 3x/week schedule.</p>
            
            <hr style="margin: 30px 0;">
            <p style="font-size: 0.9em; color: #666;">
                This is an automated message from Portfolio Management System.
            </p>
        </body>
        </html>
        """
        return await self.send_email(user_email, subject, html_content)

    async def send_drawdown_warning(
        self,
        user_email: str,
        user_name: str,
        portfolio_value: float,
        drawdown_percent: float
    ) -> bool:
        """Send portfolio drawdown warning"""
        subject = f"⚠️ Portfolio Drawdown Alert: {drawdown_percent:.1f}%"
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Portfolio Drawdown Alert</h2>
            <p>Hello {user_name},</p>
            
            <p style="color: #d32f2f; font-weight: bold;">
                Your portfolio has declined by {drawdown_percent:.1f}% from recent highs.
            </p>
            
            <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>Current Value:</strong></td>
                    <td style="padding: 10px;">${portfolio_value:,.2f} CAD</td>
                </tr>
                <tr style="border-bottom: 1px solid #ddd;">
                    <td style="padding: 10px;"><strong>Drawdown:</strong></td>
                    <td style="padding: 10px;">{drawdown_percent:.1f}%</td>
                </tr>
            </table>
            
            <p>Your risk profile is {drawdown_percent:.1f}% and maximum allowed drawdown is 30%. 
            Consider reviewing your allocation if needed.</p>
            
            <hr style="margin: 30px 0;">
            <p style="font-size: 0.9em; color: #666;">
                This is an automated message from Portfolio Management System.
            </p>
        </body>
        </html>
        """
        return await self.send_email(user_email, subject, html_content)

    async def send_transfer_recommendation(
        self,
        user_email: str,
        user_name: str,
        transfers: List[Dict]
    ) -> bool:
        """Send recommendation to transfer funds between accounts"""
        subject = "Action Required: Rebalance Your Accounts"
        
        transfers_html = ""
        for transfer in transfers:
            transfers_html += f"""
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 10px;"><strong>{transfer['from']}</strong></td>
                <td style="padding: 10px;">→</td>
                <td style="padding: 10px;"><strong>{transfer['to']}</strong></td>
                <td style="padding: 10px; text-align: right;">${transfer['amount']:,.2f}</td>
            </tr>
            """
        
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Action Required: Rebalance Your Accounts</h2>
            <p>Hello {user_name},</p>
            
            <p>To maintain your target allocation, please transfer funds between your accounts:</p>
            
            <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
                {transfers_html}
            </table>
            
            <h3>How to Transfer:</h3>
            <ol>
                <li>Log into each account (Kraken, Wealthsimple)</li>
                <li>Transfer the amounts shown above</li>
                <li>Your system will detect the transfers within the next update</li>
            </ol>
            
            <p style="color: #666; font-size: 0.9em;">
                ℹ️ These transfers are to optimize your allocation. There are no fees for transfers between your accounts.
            </p>
            
            <hr style="margin: 30px 0;">
            <p style="font-size: 0.9em; color: #666;">
                This is an automated message from Portfolio Management System.
            </p>
        </body>
        </html>
        """
        return await self.send_email(user_email, subject, html_content)

    async def send_welcome_email(
        self,
        user_email: str,
        user_name: str
    ) -> bool:
        """Send welcome email to new user"""
        subject = "Welcome to Portfolio Management System"
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Welcome, {user_name}! 🎉</h2>
            
            <p>Thank you for joining Portfolio Management System. Your automated portfolio management begins now.</p>
            
            <h3>Next Steps:</h3>
            <ol>
                <li><strong>Complete Onboarding:</strong> Connect your Kraken and Wealthsimple accounts if you have not done so.</li>
                <li><strong>Set Risk Profile:</strong> Switch to Conservative, Balanced, or Aggressive anytime.</li>
                <li><strong>Automatic Management:</strong> Our Advanced Models manage your portfolio for you.</li>
            </ol>
            
            <h3>Key Features:</h3>
            <ul>
                <li>✅ Automatic portfolio management</li>
                <li>✅ Real-time performance tracking</li>
                <li>✅ Weekly email digest with portfolio summary</li>
                <li>✅ Our models track everything, and can stop the system preserving capital and preventing losses on your account.</li>
            </ul>
            
            
            <hr style="margin: 30px 0;">
            <p style="font-size: 0.9em; color: #666;">
                Welcome aboard!<br>
                Portfolio Management System Team
            </p>
        </body>
        </html>
        """
        return await self.send_email(user_email, subject, html_content)


# Singleton instance
_email_service = None


def get_email_service() -> EmailService:
    """Get or create email service singleton"""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
