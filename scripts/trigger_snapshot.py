"""Trigger portfolio snapshot job manually."""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.models.database import SessionLocal, User, Position, PortfolioSnapshot, PerformanceSession
from datetime import datetime, timezone
import uuid
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_snapshot_sync():
    """Create portfolio snapshots synchronously."""
    db = SessionLocal()
    try:
        # Get all users with positions
        users_with_positions = (
            db.query(User)
            .join(Position, Position.user_id == User.id)
            .distinct()
            .all()
        )
        
        if not users_with_positions:
            logger.info("[SKIP] No users with positions found")
            return 0
        
        logger.info(f"Found {len(users_with_positions)} users with positions")
        snapshot_count = 0
        
        for user in users_with_positions:
            # Get all positions for this user
            positions = db.query(Position).filter(Position.user_id == user.id).all()
            
            if not positions:
                continue
            
            # Calculate totals from actual positions
            total_value = sum(p.market_value or 0 for p in positions)
            
            # Categorize by asset type
            crypto_value = 0
            stocks_value = 0
            cash_value = 0
            
            for p in positions:
                symbol = (p.symbol or "").upper()
                if symbol in ["CAD", "USD", "CASH"]:
                    cash_value += p.market_value or 0
                elif symbol.endswith(".TO") or "." in symbol:
                    stocks_value += p.market_value or 0
                else:
                    crypto_value += p.market_value or 0
            
            if total_value <= 0:
                logger.info(f"Skipping user {user.email} - no value")
                continue
            
            # Build positions snapshot
            positions_snapshot = {}
            for p in positions:
                positions_snapshot[p.symbol] = {
                    "quantity": float(p.quantity or 0),
                    "price": float(p.current_price or 0),
                    "value": float(p.market_value or 0),
                }
            
            # Check for existing session or create one
            session = db.query(PerformanceSession).filter(
                PerformanceSession.user_id == user.id,
                PerformanceSession.is_active == True
            ).first()
            
            if not session:
                session = PerformanceSession(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    started_at=datetime.now(timezone.utc),
                    is_active=True,
                    initial_value=total_value,
                    metadata_json={"source": "snapshot_job"}
                )
                db.add(session)
                db.commit()
                logger.info(f"Created new session for user {user.email}")
            
            # Create snapshot
            snapshot = PortfolioSnapshot(
                id=str(uuid.uuid4()),
                user_id=user.id,
                total_value=total_value,
                crypto_value=crypto_value,
                stocks_value=stocks_value,
                cash_value=cash_value,
                daily_return=0.0,
                cumulative_return=0.0,
                positions_snapshot=positions_snapshot,
                allocation_snapshot={},
                timestamp=datetime.now(timezone.utc),
                interval="4h"
            )
            db.add(snapshot)
            db.commit()
            
            snapshot_count += 1
            logger.info(f"Created snapshot for user {user.email}: ${total_value:.2f}")
        
        logger.info(f"[OK] Created {snapshot_count} snapshots")
        return snapshot_count
        
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 50)
    print("Triggering Portfolio Snapshot Job")
    print("=" * 50)
    
    count = create_snapshot_sync()
    
    # Verify
    db = SessionLocal()
    total = db.query(PortfolioSnapshot).count()
    db.close()
    
    print(f"\nResult: Created {count} new snapshots")
    print(f"Total snapshots in database: {total}")
