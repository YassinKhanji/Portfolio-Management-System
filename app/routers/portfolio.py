"""
Portfolio Management Router

Handles portfolio metrics, positions, transactions, and allocation queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel

from ..models.database import SessionLocal, User, Position, Transaction
from ..routers.auth import get_current_user, oauth2_scheme

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["portfolio"])


# Request Models
class UpdateRiskProfileRequest(BaseModel):
    risk_profile: str


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
        total_value = sum(p.market_value for p in positions if p.market_value)
        
        # Calculate 24h change (simplified - would need price history)
        # TODO: Implement proper 24h change calculation with historical prices
        change_24h = 2.34  # Placeholder
        
        # Get recent transaction count
        yesterday = datetime.utcnow() - timedelta(days=1)
        transactions_24h = db.query(Transaction).filter(
            Transaction.created_at >= yesterday
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
        
        positions = query.order_by(Position.market_value.desc()).limit(limit).all()
        
        result = []
        for pos in positions:
            result.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "name": pos.symbol,
                "type": "asset",
                "balance": pos.quantity,
                "price": pos.price,
                "value": pos.market_value,
                "change_24h": 0.0,
                "allocation_percent": pos.allocation_percentage or 0.0
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
        
        transactions = query.order_by(Transaction.created_at.desc()).limit(limit).all()
        
        result = []
        for txn in transactions:
            result.append({
                "id": txn.id,
                "asset": txn.symbol,
                "type": txn.side,
                "amount": txn.quantity,
                "price": txn.price,
                "total": txn.quantity * txn.price,
                "timestamp": txn.executed_at.isoformat() + "Z" if txn.executed_at else txn.created_at.isoformat() + "Z",
                "status": txn.status or "pending"
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
        total_value = sum(p.market_value for p in positions if p.market_value) or 1
        
        # Group by asset type - since asset_class doesn't exist in DB, 
        # we'll just return by symbol
        allocation_by_asset = []
        
        for pos in positions:
            value = pos.market_value or 0
            
            # By asset
            allocation_by_asset.append({
                "symbol": pos.symbol,
                "value": value,
                "percent": round(value / total_value * 100, 2)
            })
        
        # Convert to list format for frontend
        allocation_by_type_list = []
        
        return {
            "by_type": allocation_by_type_list,
            "by_asset": sorted(allocation_by_asset, key=lambda x: x['value'], reverse=True)[:10],
            "total_value": total_value
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch allocation data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/risk-metrics")
async def get_risk_metrics(
    returnMethod: str = "TWR",
    period: str = "1Y",
    lookback: int = 90,
    db: Session = Depends(get_db)
):
    """
    Calculate risk metrics (Sharpe, Sortino, Calmar ratios, volatility, max drawdown)
    
    Args:
        returnMethod: TWR or MWR
        period: Return period (1D, 1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y)
        lookback: Number of days for volatility/risk calculation (30, 90, 365)
        
    Returns:
        Risk metrics including Sharpe ratio, Sortino ratio, Calmar ratio, volatility, max drawdown
    """
    try:
        logger.info(f"Fetching risk metrics (method={returnMethod}, period={period}, lookback={lookback})")
        
        # Calculate portfolio returns from snapshots
        from ..models.database import PortfolioSnapshot
        from ..core.config import get_settings
        import numpy as np
        
        config = get_settings()
        lookback_date = datetime.utcnow() - timedelta(days=lookback)
        
        # Get portfolio snapshots for lookback period
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.recorded_at >= lookback_date
        ).order_by(PortfolioSnapshot.recorded_at).all()
        
        if len(snapshots) < 2:
            return {
                "sharpeRatio": None,
                "sortinoRatio": None,
                "calmarRatio": None,
                "maxDrawdown": None,
                "volatility": None,
                "returnPercent": 0,
                "message": "Insufficient data for metrics"
            }
        
        # Extract daily returns
        values = [s.total_value for s in snapshots]
        dates = [s.recorded_at for s in snapshots]
        
        # Calculate daily returns
        returns = []
        for i in range(1, len(values)):
            if values[i-1] > 0:
                daily_return = (values[i] - values[i-1]) / values[i-1]
                returns.append(daily_return)
        
        if not returns:
            return {
                "sharpeRatio": None,
                "sortinoRatio": None,
                "calmarRatio": None,
                "maxDrawdown": None,
                "volatility": None,
                "returnPercent": 0,
                "message": "Insufficient data for metrics"
            }
        
        returns_array = np.array(returns)
        
        # Calculate metrics
        total_return_pct = ((values[-1] - values[0]) / values[0]) * 100 if values[0] > 0 else 0
        daily_volatility = np.std(returns_array)
        annual_volatility = daily_volatility * np.sqrt(252)  # Annualize
        
        # Sharpe ratio (risk-free rate from config)
        risk_free_rate = config.RISK_FREE_RATE
        excess_returns = returns_array - (risk_free_rate / 252)
        sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0
        
        # Sortino ratio (only downside volatility)
        downside_returns = returns_array[returns_array < 0]
        downside_volatility = np.std(downside_returns) if len(downside_returns) > 0 else 0
        sortino_ratio = (np.mean(excess_returns) / downside_volatility * np.sqrt(252)) if downside_volatility > 0 else 0
        
        # Calmar ratio (return / max drawdown)
        cumulative_returns = np.cumprod(1 + returns_array)
        running_max = np.maximum.accumulate(cumulative_returns)
        drawdown = (cumulative_returns - running_max) / running_max
        max_drawdown = np.min(drawdown) if len(drawdown) > 0 else 0
        
        calmar_ratio = (total_return_pct / 100) / abs(max_drawdown) if max_drawdown < 0 else 0
        
        return {
            "returnPercent": round(total_return_pct, 2),
            "volatility": round(annual_volatility * 100, 2),
            "sharpeRatio": round(sharpe_ratio, 2),
            "sortinoRatio": round(sortino_ratio, 2),
            "calmarRatio": round(calmar_ratio, 2),
            "maxDrawdown": round(max_drawdown, 4),
            "lookbackDays": lookback,
            "snapshotCount": len(snapshots),
        }
        
    except Exception as e:
        logger.error(f"Failed to calculate risk metrics: {str(e)}")
        return {
            "sharpeRatio": None,
            "sortinoRatio": None,
            "calmarRatio": None,
            "maxDrawdown": None,
            "volatility": None,
            "returnPercent": None,
            "error": str(e)
        }


@router.get("/portfolio/risk-profile")
async def get_risk_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's current risk profile
    
    Args:
        current_user: Authenticated user
        db: Database session
        
    Returns:
        User's risk profile
    """
    try:
        logger.info(f"Fetching risk profile for user {current_user.email}")
        
        return {
            "id": current_user.id,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "risk_profile": current_user.risk_profile,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch risk profile: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch risk profile"
        )


@router.put("/portfolio/risk-profile")
async def update_risk_profile(
    request: UpdateRiskProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update user's risk profile (Conservative, Balanced, Aggressive)
    
    Args:
        request: Risk profile update request
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Updated user information
    """
    try:
        # Validate risk profile
        valid_profiles = ["Conservative", "Balanced", "Aggressive"]
        if request.risk_profile not in valid_profiles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid risk profile. Must be one of: {', '.join(valid_profiles)}"
            )
        
        # Update user's risk profile
        current_user.risk_profile = request.risk_profile
        current_user.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(current_user)
        
        logger.info(f"Updated risk profile for user {current_user.email}: {request.risk_profile}")
        
        return {
            "id": current_user.id,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "risk_profile": current_user.risk_profile,
            "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update risk profile: {str(e)}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update risk profile"
        )
