"""
Portfolio Snapshot Job

Creates portfolio snapshots every 4 hours for historical tracking and performance charts.
Snapshots are taken in user's local timezone.
"""

from app.models.database import SessionLocal, User, Position, PortfolioSnapshot
from datetime import datetime
from typing import Optional
import logging
import uuid

logger = logging.getLogger(__name__)


async def create_snapshots():
    """
    Create portfolio snapshots for all active users.
    Called every 4 hours by APScheduler.
    """
    db = SessionLocal()
    try:
        # Get all active users
        active_users = db.query(User).filter(User.is_active == True, User.is_onboarded == True).all()
        
        snapshot_count = 0
        error_count = 0
        
        for user in active_users:
            try:
                # Get all positions for this user
                positions = db.query(Position).filter(Position.user_id == user.id).all()
                
                if not positions:
                    continue  # Skip if no positions
                
                # Calculate totals
                total_value = sum(p.market_value for p in positions)
                crypto_value = sum(p.market_value for p in positions if p.asset_class == "crypto")
                stocks_value = sum(p.market_value for p in positions if p.asset_class == "stocks")
                cash_value = sum(p.market_value for p in positions if p.asset_class == "cash")
                
                # Build positions and allocation snapshots (JSON)
                positions_snapshot = {}
                allocation_snapshot = {}
                
                for pos in positions:
                    positions_snapshot[pos.symbol] = {
                        "quantity": float(pos.quantity),
                        "price": float(pos.price),
                        "market_value": float(pos.market_value),
                        "asset_class": pos.asset_class,
                    }
                    
                    if total_value > 0:
                        allocation_snapshot[pos.symbol] = float(pos.market_value) / total_value
                
                # Calculate daily return (from last snapshot)
                last_snapshot = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.user_id == user.id)
                    .order_by(PortfolioSnapshot.recorded_at.desc())
                    .first()
                )
                
                daily_return = 0.0
                daily_return_pct = 0.0
                
                if last_snapshot:
                    daily_return = total_value - last_snapshot.total_value
                    if last_snapshot.total_value > 0:
                        daily_return_pct = (daily_return / last_snapshot.total_value) * 100
                
                # Create snapshot record
                snapshot = PortfolioSnapshot(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    total_value=total_value,
                    crypto_value=crypto_value,
                    stocks_value=stocks_value,
                    cash_value=cash_value,
                    daily_return=daily_return,
                    daily_return_pct=daily_return_pct,
                    positions_snapshot=positions_snapshot,
                    allocation_snapshot=allocation_snapshot,
                    recorded_at=datetime.utcnow(),
                    aggregation_level="4h"
                )
                
                db.add(snapshot)
                snapshot_count += 1
                
            except Exception as e:
                logger.error(f"Failed to create snapshot for user {user.id}: {str(e)}", exc_info=True)
                error_count += 1
        
        db.commit()
        logger.info(f"[OK] Portfolio snapshots created: {snapshot_count} success, {error_count} errors")
        
    except Exception as e:
        logger.error(f"Failed to create portfolio snapshots: {str(e)}", exc_info=True)
        db.rollback()
    finally:
        db.close()
