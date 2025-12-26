"""
Daily Rebalance Job

Rebalances all user portfolios based on current regime.
"""

from app.models.database import Session, User, Position, Alert, Log
from app.trading.portfolio_calculator import PortfolioCalculator
from app.trading.executor import TradeExecutor
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def rebalance_portfolios():
    """Rebalance all active portfolios"""
    
    db = Session()
    rebalanced_count = 0
    error_count = 0
    
    try:
        # Get all active users
        active_users = db.query(User).filter(User.is_active == True).all()
        logger.info(f"Rebalancing {len(active_users)} active portfolios...")
        
        for user in active_users:
            try:
                # Calculate target allocation
                calculator = PortfolioCalculator()
                target_allocation = calculator.calculate_target_allocation(
                    total_portfolio_value=user.total_value,
                    risk_profile=user.risk_profile
                )
                
                # Get current positions
                current_positions = db.query(Position).filter(
                    Position.user_id == user.id,
                    Position.quantity > 0
                ).all()
                
                # Calculate required trades
                required_trades = calculator.calculate_required_trades(
                    current_positions,
                    target_allocation
                )
                
                if required_trades:
                    # Execute trades
                    executor = TradeExecutor(user.snaptrade_user_token)
                    executor.execute_trades(required_trades, user.id)
                    
                    rebalanced_count += 1
                    logger.info(f"✓ Rebalanced portfolio for user {user.id}")
                
            except Exception as e:
                error_count += 1
                logger.error(f"Failed to rebalance user {user.id}: {str(e)}")
                
                # Create alert for rebalance failure
                alert = Alert(
                    alert_type="REBALANCE_FAILED",
                    severity="HIGH",
                    message=f"Failed to rebalance portfolio: {str(e)}",
                    user_id=user.id,
                    action_required=True
                )
                db.add(alert)
        
        # Log the job result
        log = Log(
            timestamp=datetime.utcnow(),
            level="info",
            message=f"Daily rebalance completed: {rebalanced_count} successful, {error_count} failed",
            component="daily_rebalance_job",
            metadata_json={
                "rebalanced_count": rebalanced_count,
                "error_count": error_count
            }
        )
        db.add(log)
        db.commit()
        
        logger.info(f"Daily rebalance completed: {rebalanced_count} successful, {error_count} errors")
        
    except Exception as e:
        logger.error(f"Rebalance job failed: {str(e)}")
        
        # Log the critical error
        try:
            log = Log(
                timestamp=datetime.utcnow(),
                level="critical",
                message=f"Rebalance job failed: {str(e)}",
                component="daily_rebalance_job"
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
