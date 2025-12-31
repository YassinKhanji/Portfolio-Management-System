"""
Rebalancing API Endpoints

Endpoints:
- POST /api/rebalance/{user_id} - Trigger rebalance for single user
- POST /api/rebalance/all - Trigger rebalance for all users (admin)
- GET /api/rebalance/status/{user_id} - Get rebalance status
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
import logging
import uuid

from ..models.database import SessionLocal, User, Transaction, Position

router = APIRouter(prefix="/api", tags=["rebalancing"])
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/rebalance/{user_id}")
async def rebalance_user(
    user_id: str,
    force: bool = Query(False, description="Force rebalance even if no drift"),
    dry_run: bool = Query(False, description="Calculate but don't execute"),
    db: Session = Depends(get_db)
):
    """
    Trigger rebalancing for a single user.
    
    Args:
        user_id: User ID to rebalance
        force: Force rebalance even if no drift detected
        dry_run: Calculate but don't execute trades
        
    Returns:
        {
            "status": "queued|processing|completed|failed",
            "user_id": "...",
            "positions_before": [...],
            "positions_after": [...],
            "trades_calculated": [...],
            "message": "..."
        }
    """
    try:
        logger.info("Rebalance requested")
        
        # Verify user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        
        # Get current positions
        current_positions = db.query(Position).filter(Position.user_id == user_id).all()
        positions_data = [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "price": p.price,
                "value": p.market_value,
                "target_allocation": p.target_percentage,
                "current_allocation": p.allocation_percentage
            }
            for p in current_positions
        ]
        
        total_value = sum(p.market_value for p in current_positions)
        
        # Calculate drift (current allocation vs target allocation)
        drift_data = []
        for pos in current_positions:
            drift = abs(pos.allocation_percentage - pos.target_percentage)
            if drift > 5.0 or force:  # 5% drift threshold
                drift_data.append({
                    "symbol": pos.symbol,
                    "current": pos.allocation_percentage,
                    "target": pos.target_percentage,
                    "drift": drift,
                    "action": "BUY" if pos.allocation_percentage < pos.target_percentage else "SELL"
                })
        
        # Calculate trades needed (simple proportional rebalancing)
        trades = []
        if drift_data:
            for item in drift_data:
                drift_value = (item["target"] - item["current"]) * total_value / 100
                # Find position to get current price
                pos = next(p for p in current_positions if p.symbol == item["symbol"])
                if pos.price > 0:
                    quantity = abs(drift_value) / pos.price
                    trades.append({
                        "symbol": item["symbol"],
                        "action": item["action"],
                        "quantity": quantity,
                        "estimated_value": drift_value,
                        "status": "calculated"
                    })
        
        # If not dry run, execute trades
        if not dry_run and trades:
            for trade in trades:
                transaction = Transaction(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    symbol=trade["symbol"],
                    quantity=trade["quantity"],
                    price=next(p.price for p in current_positions if p.symbol == trade["symbol"]),
                    side=trade["action"],
                    status="executed",
                    created_at=datetime.now(timezone.utc),
                    executed_at=datetime.now(timezone.utc)
                )
                db.add(transaction)
                trade["status"] = "executed"
        
        if trades:
            db.commit()
        
        return {
            "status": "completed" if not dry_run else "calculated",
            "user_id": user_id,
            "dry_run": dry_run,
            "positions_before": positions_data,
            "drift_detected": drift_data,
            "trades_calculated": trades,
            "total_portfolio_value": total_value,
            "message": f"Rebalance {'queued' if dry_run else 'completed'} with {len(trades)} trades"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rebalance failed", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebalance/all")
async def rebalance_all_users(
    dry_run: bool = Query(False, description="Calculate but don't execute"),
    db: Session = Depends(get_db)
):
    """
    Trigger rebalancing for ALL users (admin endpoint).
    
    Args:
        dry_run: Calculate but don't execute
        
    Returns:
        {
            "status": "queued",
            "users_count": 42,
            "rebalanced": 38,
            "failed": 4,
            "timestamp": "2024-01-15T10:00:00Z"
        }
    """
    try:
        logger.info(f"Bulk rebalance requested for all users (dry_run={dry_run})")
        
        # Get all active users
        users = db.query(User).filter(User.active == True).all()
        
        rebalanced = 0
        failed = 0
        
        for user in users:
            try:
                # Get positions for user
                positions = db.query(Position).filter(Position.user_id == user.id).all()
                if not positions:
                    continue
                
                # Simple drift check
                total_value = sum(p.market_value for p in positions)
                has_drift = any(abs(p.allocation_percentage - p.target_percentage) > 5.0 for p in positions)
                
                if has_drift:
                    # Calculate and execute rebalancing
                    for pos in positions:
                        drift = pos.target_percentage - pos.allocation_percentage
                        if drift != 0:
                            quantity = (drift * total_value / 100) / pos.price if pos.price > 0 else 0
                            if not dry_run and quantity != 0:
                                transaction = Transaction(
                                    id=str(uuid.uuid4()),
                                    user_id=user.id,
                                    symbol=pos.symbol,
                                    quantity=abs(quantity),
                                    price=pos.price,
                                    side="BUY" if drift > 0 else "SELL",
                                    status="executed",
                                    created_at=datetime.now(timezone.utc),
                                    executed_at=datetime.now(timezone.utc)
                                )
                                db.add(transaction)
                    
                    rebalanced += 1
            except Exception as e:
                logger.error("Failed to rebalance user", exc_info=True)
                failed += 1
                continue
        
        if not dry_run:
            db.commit()
        
        return {
            "status": "completed" if not dry_run else "calculated",
            "users_checked": len(users),
            "users_rebalanced": rebalanced,
            "users_failed": failed,
            "dry_run": dry_run,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # PLACEHOLDER: Bulk rebalancing - integrate with portfolio manager
        
        return {
            "status": "queued",
            "users_count": 42,
            "timestamp": "2024-01-15T10:00:00Z",
            "message": "Bulk rebalance queued"
        }
    except Exception as e:
        logger.error("Bulk rebalance failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/calculate/{user_id}")
async def calculate_portfolio(user_id: str):
    """
    Calculate target allocation WITHOUT executing trades.
    
    Returns:
        {
            "user_id": "user123",
            "btc_target": 0.40,
            "eth_target": 0.25,
            "alt_target": 0.20,
            "stable_target": 0.15,
            "total_value": 100000.00,
            "current_regime": "BULL",
            "trades_needed": [...]
        }
    """
    try:
        logger.info("Portfolio calculation requested")
        
        # PLACEHOLDER: Integrate with allocation calculator
        
        return {
            "user_id": user_id,
            "btc_target": 0.40,
            "eth_target": 0.25,
            "alt_target": 0.20,
            "stable_target": 0.15,
            "total_value": 100000.00,
            "current_regime": "BULL",
            "trades_needed": []
        }
    except Exception as e:
        logger.error("Portfolio calculation failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
