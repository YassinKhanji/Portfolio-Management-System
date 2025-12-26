"""
Portfolio Management Router

Handles portfolio metrics, positions, transactions, and allocation queries.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
import logging

from ..models.database import SessionLocal, User, Position, Transaction

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/portfolio/metrics")
async def get_portfolio_metrics(db: Session = Depends(get_db)):
    """
    Get overall portfolio metrics for dashboard
    
    Returns:
        Portfolio statistics including total value, 24h change, positions count, etc.
    """
    try:
        logger.info("Fetching portfolio metrics...")
        
        # Query database for portfolio data
        total_users = db.query(User).count()
        total_positions = db.query(Position).count()
        
        # Calculate total portfolio value from all user positions
        positions = db.query(Position).all()
        total_value = sum(p.current_value for p in positions if p.current_value)
        
        # Calculate 24h change (simplified - would need price history)
        # TODO: Implement proper 24h change calculation with historical prices
        change_24h = 2.34  # Placeholder
        
        # Get recent transaction count
        yesterday = datetime.utcnow() - timedelta(days=1)
        transactions_24h = db.query(Transaction).filter(
            Transaction.timestamp >= yesterday
        ).count()
        
        return {
            "total_value": total_value,
            "change_24h": change_24h,
            "change_24h_percent": (change_24h / total_value * 100) if total_value > 0 else 0,
            "total_positions": total_positions,
            "active_users": total_users,
            "transactions_24h": transactions_24h,
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch portfolio metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/positions")
async def get_portfolio_positions(
    user_id: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    Get portfolio positions/assets
    
    Args:
        user_id: Optional user ID to filter by
        limit: Maximum number of positions to return
        
    Returns:
        List of portfolio positions with current values
    """
    try:
        logger.info(f"Fetching positions (user_id={user_id}, limit={limit})...")
        
        query = db.query(Position)
        
        if user_id:
            query = query.filter(Position.user_id == user_id)
        
        positions = query.order_by(Position.current_value.desc()).limit(limit).all()
        
        result = []
        for pos in positions:
            result.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "name": pos.symbol,  # TODO: Get full name from asset metadata
                "type": pos.asset_type or "Crypto",
                "balance": pos.quantity,
                "price": pos.current_price,
                "value": pos.current_value,
                "change_24h": pos.change_24h or 0.0,
                "allocation_percent": pos.allocation_percent or 0.0
            })
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch positions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/transactions")
async def get_portfolio_transactions(
    user_id: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    Get recent portfolio transactions
    
    Args:
        user_id: Optional user ID to filter by
        limit: Maximum number of transactions to return
        
    Returns:
        List of recent transactions
    """
    try:
        logger.info(f"Fetching transactions (user_id={user_id}, limit={limit})...")
        
        query = db.query(Transaction)
        
        if user_id:
            query = query.filter(Transaction.user_id == user_id)
        
        transactions = query.order_by(Transaction.timestamp.desc()).limit(limit).all()
        
        result = []
        for txn in transactions:
            result.append({
                "id": txn.id,
                "asset": txn.symbol,
                "type": txn.transaction_type,  # buy, sell, rebalance
                "amount": txn.quantity,
                "price": txn.price,
                "total": txn.total_value,
                "timestamp": txn.timestamp.isoformat() + "Z" if txn.timestamp else None,
                "status": txn.status or "completed"
            })
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch transactions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/performance")
async def get_portfolio_performance(
    user_id: Optional[str] = None,
    period: str = "30d",
    db: Session = Depends(get_db)
):
    """
    Get portfolio performance data for charts
    
    Args:
        user_id: Optional user ID to filter by
        period: Time period (7d, 30d, 90d, 1y, all)
        
    Returns:
        Time series data for performance chart
    """
    try:
        logger.info(f"Fetching performance data (period={period})...")
        
        # TODO: Implement actual performance calculation from transaction history
        # For now, return sample data structure
        
        # Generate sample dates
        days_map = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "all": 365}
        days = days_map.get(period, 30)
        
        data = []
        base_value = 100000
        
        for i in range(days):
            date = datetime.utcnow() - timedelta(days=days-i)
            # Simple upward trend with noise
            value = base_value * (1 + 0.001 * i + 0.02 * (i % 7 - 3) / 7)
            
            data.append({
                "date": date.isoformat() + "Z",
                "value": round(value, 2),
                "benchmark": round(base_value * (1 + 0.0008 * i), 2)  # Slower benchmark
            })
        
        return {
            "period": period,
            "data": data,
            "total_return": 15.4,  # Placeholder
            "benchmark_return": 8.2  # Placeholder
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch performance data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/allocation")
async def get_portfolio_allocation(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get portfolio allocation breakdown for charts
    
    Args:
        user_id: Optional user ID to filter by
        
    Returns:
        Allocation by asset type and by individual assets
    """
    try:
        logger.info(f"Fetching allocation data (user_id={user_id})...")
        
        query = db.query(Position)
        
        if user_id:
            query = query.filter(Position.user_id == user_id)
        
        positions = query.all()
        
        # Calculate total value
        total_value = sum(p.current_value for p in positions if p.current_value) or 1
        
        # Group by asset type
        allocation_by_type = {}
        allocation_by_asset = []
        
        for pos in positions:
            asset_type = pos.asset_type or "Crypto"
            value = pos.current_value or 0
            
            # By type
            if asset_type not in allocation_by_type:
                allocation_by_type[asset_type] = 0
            allocation_by_type[asset_type] += value
            
            # By asset
            allocation_by_asset.append({
                "symbol": pos.symbol,
                "value": value,
                "percent": round(value / total_value * 100, 2)
            })
        
        # Convert to list format for frontend
        allocation_by_type_list = [
            {
                "category": asset_type,
                "value": value,
                "percent": round(value / total_value * 100, 2)
            }
            for asset_type, value in allocation_by_type.items()
        ]
        
        return {
            "by_type": allocation_by_type_list,
            "by_asset": sorted(allocation_by_asset, key=lambda x: x['value'], reverse=True)[:10],
            "total_value": total_value
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch allocation data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
