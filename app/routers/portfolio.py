"""
Portfolio Management Router

Handles portfolio metrics, positions, transactions, and allocation queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List, Dict
from datetime import datetime, timedelta, timezone
import logging

import yfinance as yf
from pydantic import BaseModel

from ..models.database import (
    SessionLocal,
    User,
    Position,
    Transaction,
    Connection,
    PortfolioSnapshot,
    RiskProfile,
    SystemStatus,
    Alert,
    AlertPreference,
    Log,
)
from ..routers.auth import get_current_user, oauth2_scheme
from ..core.config import get_settings

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


# Benchmark cache for S&P 500 (5Y daily)
_benchmark_cache: Dict[str, object] = {
    "fetched_at": None,
    "data": [],  # list of {date: datetime.date, close: float}
}

_benchmark_intraday_cache: Dict[str, object] = {
    "fetched_at": None,
    "data": [],  # list of {timestamp: datetime, close: float}
}


def _fetch_sp500_history() -> List[dict]:
    """Fetch last 5y of SP500 (\^GSPC) daily closes, cached for reuse."""
    try:
        df = yf.download("^GSPC", period="5y", interval="1d", progress=False)
        if df is None or df.empty:
            logger.warning("yfinance returned empty data for ^GSPC")
            return []

        df = df.reset_index()[["Date", "Close"]]
        records = []
        for _, row in df.iterrows():
            day = row["Date"].date() if hasattr(row["Date"], "date") else row["Date"]
            records.append({"date": day, "close": float(row["Close"])})
        return records
    except Exception as exc:  # pragma: no cover - network dependency
        logger.error(f"Failed to fetch ^GSPC from yfinance: {exc}")
        return []


def _fetch_sp500_intraday(days: int = 7) -> List[dict]:
    """Fetch intraday S&P 500 (hourly) closes for the last N days."""
    try:
        df = yf.download("^GSPC", period=f"{days}d", interval="60m", progress=False)
        if df is None or df.empty:
            logger.warning("yfinance returned empty intraday data for ^GSPC")
            return []
        records = []
        for idx, row in df.iterrows():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            records.append({"timestamp": ts, "close": float(row.get("Close"))})
        return records
    except Exception as exc:  # pragma: no cover - network dependency
        logger.error(f"Failed to fetch intraday ^GSPC from yfinance: {exc}")
        return []


def _get_sp500_history() -> List[dict]:
    """Return cached SP500 history or refresh if stale (>6h) or empty."""
    fetched_at = _benchmark_cache.get("fetched_at")
    data = _benchmark_cache.get("data") or []

    stale = True
    if fetched_at:
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        stale = age_hours > 6

    if stale or not data:
        data = _fetch_sp500_history()
        if data:
            _benchmark_cache["fetched_at"] = datetime.now(timezone.utc)
            _benchmark_cache["data"] = data
    return data


def _get_sp500_intraday() -> List[dict]:
    """Return cached intraday SP500 history (hourly) or refresh if stale (>1h)."""
    fetched_at = _benchmark_intraday_cache.get("fetched_at")
    data = _benchmark_intraday_cache.get("data") or []

    stale = True
    if fetched_at:
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        stale = age_hours > 1

    if stale or not data:
        data = _fetch_sp500_intraday()
        if data:
            _benchmark_intraday_cache["fetched_at"] = datetime.now(timezone.utc)
            _benchmark_intraday_cache["data"] = data
    return data


@router.get("/clients")
async def list_clients(db: Session = Depends(get_db)):
    """
    Return all clients with basic portfolio totals and activity metadata.
    """
    try:
        users = db.query(User).all()
        settings = get_settings()

        # Sum portfolio value per user
        value_by_user = dict(
            db.query(Position.user_id, func.coalesce(func.sum(Position.market_value), 0.0))
            .group_by(Position.user_id)
            .all()
        )

        now = datetime.now(timezone.utc)
        clients = []
        risk_counts = {"Aggressive": 0, "Balanced": 0, "Conservative": 0}
        active_today = 0
        total_aum = 0.0

        def _aware(dt: Optional[datetime]) -> datetime:
            # Normalize datetimes that may have been stored without tzinfo
            if dt is None:
                return now
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

        for u in users:
            total_value = float(value_by_user.get(u.id, 0.0))
            total_aum += total_value

            risk_raw = (u.risk_profile or "Balanced").lower()
            risk_map = {
                "high": "Aggressive",
                "aggressive": "Aggressive",
                "medium": "Balanced",
                "balanced": "Balanced",
                "low": "Conservative",
                "conservative": "Conservative",
            }
            risk = risk_map.get(risk_raw, "Balanced")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1

            last_active_dt = _aware(u.updated_at) if u.updated_at else _aware(u.created_at)
            if (now - last_active_dt) <= timedelta(days=1):
                active_today += 1

            is_owner = (u.email == getattr(settings, "ADMIN_EMAIL", ""))
            status_label = "Owner" if is_owner else ("Admin" if (u.role or "").lower() == "admin" else "Client")

            clients.append({
                "id": u.id,
                "name": u.full_name or u.email,
                "email": u.email,
                "role": u.role or "client",
                "status": status_label,
                "risk_profile": risk,
                "total_value": total_value,
                "active": bool(u.active),
                "last_active": _aware(last_active_dt).isoformat(),
                "created_at": (_aware(u.created_at).isoformat() if u.created_at else None),
                "is_owner": is_owner,
                "is_admin": (u.role or "").lower() == "admin" or is_owner,
            })

        # Simple numeric risk score: High=10, Medium=5, Low=1
        risk_score_map = {"Aggressive": 10, "Balanced": 5, "Conservative": 1}
        if clients:
            avg_risk_score = sum(risk_score_map.get(c["risk_profile"], 5) for c in clients) / len(clients)
        else:
            avg_risk_score = 0.0

        return {
            "clients": clients,
            "summary": {
                "total_clients": len(clients),
                "total_aum": total_aum,
                "active_today": active_today,
                "avg_risk_score": avg_risk_score,
            },
            "risk_breakdown": risk_counts,
        }

    except Exception as e:
        logger.error(f"Failed to fetch clients: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/health-metrics")
async def get_portfolio_health_metrics(
    period: str = "YTD",
    risk_profile: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Return portfolio health metrics for the Return Health page using live data.

    - Aggregates PortfolioSnapshot data for cumulative growth and ROI.
    - Derives monthly returns from the aggregated growth curve.
    - Builds KPI cards from risk metrics computed over recent snapshots.
    - Estimates asset-class contribution using current positions.
    """
    try:
        now = datetime.now(timezone.utc)

        # Determine lookback window
        upper = period.upper()
        if upper == "YTD":
            start_date = datetime(now.year, 1, 1)
        else:
            days_map = {
                "7D": 7,
                "1M": 30,
                "3M": 90,
                "6M": 180,
                "1Y": 365,
                "ALL": 5 * 365,
            }
            days = days_map.get(upper, 30)
            start_date = now - timedelta(days=days)

        normalized_risk = _normalize_risk(risk_profile) if risk_profile else None
        user_query = db.query(User)
        if normalized_risk:
            user_query = user_query.filter(func.lower(User.risk_profile) == normalized_risk.lower())

        users = user_query.all()
        if not users:
            return {
                "period": period,
                "kpis": [],
                "growth": [],
                "monthly_returns": [],
                "asset_performance": [],
            }

        created_map = {u.id: (u.created_at or now) for u in users}
        user_ids = list(created_map.keys())

        snapshots_q = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.user_id.in_(user_ids))
        snapshots_q = snapshots_q.filter(PortfolioSnapshot.recorded_at >= start_date)
        snapshots = snapshots_q.order_by(PortfolioSnapshot.recorded_at).all()

        if not snapshots:
            return {
                "period": period,
                "kpis": [],
                "growth": [],
                "monthly_returns": [],
                "asset_performance": [],
            }

        # Aggregate portfolio value per day, respecting user creation dates
        by_day: dict = {}
        for snap in snapshots:
            created_at = created_map.get(snap.user_id, snap.recorded_at)
            if snap.recorded_at < created_at:
                continue
            day = snap.recorded_at.date()
            by_day[day] = by_day.get(day, 0.0) + float(snap.total_value or 0.0)

        if not by_day:
            return {
                "period": period,
                "kpis": [],
                "growth": [],
                "monthly_returns": [],
                "asset_performance": [],
            }

        benchmark_series = _get_sp500_history()
        benchmark_map = {row["date"]: row["close"] for row in benchmark_series}

        sorted_days = sorted(by_day.keys())
        growth = []
        last_bench = None
        for day in sorted_days:
            bench_val = benchmark_map.get(day, last_bench)
            if bench_val is not None:
                last_bench = bench_val

            dt = datetime.combine(day, datetime.min.time())
            growth.append({
                "date": dt.isoformat() + "Z",
                "portfolio": round(by_day[day], 2),
                "benchmark": bench_val,
            })

        first_val = growth[0]["portfolio"] if growth else 0
        last_val = growth[-1]["portfolio"] if growth else 0
        total_return_pct = ((last_val - first_val) / first_val * 100) if first_val else 0.0

        # Derive monthly returns from growth curve
        monthly_windows = {}
        for point in growth:
            dt = datetime.fromisoformat(point["date"].replace("Z", ""))
            key = dt.strftime("%Y-%m")
            if key not in monthly_windows:
                monthly_windows[key] = {"first": point["portfolio"], "last": point["portfolio"], "month": dt.strftime("%b")}
            else:
                monthly_windows[key]["last"] = point["portfolio"]

        monthly_returns = []
        for key in sorted(monthly_windows.keys()):
            window = monthly_windows[key]
            start_val = window["first"]
            end_val = window["last"]
            pct = ((end_val - start_val) / start_val * 100) if start_val else 0.0
            monthly_returns.append({"month": window["month"], "value": round(pct, 2)})

        # Compute basic risk metrics from snapshots (reuse logic from risk endpoint)
        try:
            import numpy as np
        except ImportError:
            np = None

        sharpe_ratio = None
        annual_volatility_pct = None
        max_drawdown_pct = None

        if np and len(snapshots) >= 2:
            values = [s.total_value for s in snapshots]
            returns = []
            for i in range(1, len(values)):
                if values[i-1] and values[i-1] != 0:
                    returns.append((values[i] - values[i-1]) / values[i-1])

            if returns:
                returns_array = np.array(returns)
                config = get_settings()
                risk_free_rate = getattr(config, "RISK_FREE_RATE", 0.0)

                daily_volatility = np.std(returns_array)
                annual_volatility = daily_volatility * np.sqrt(252)
                annual_volatility_pct = round(float(annual_volatility * 100), 2)

                excess_returns = returns_array - (risk_free_rate / 252)
                if np.std(excess_returns) > 0:
                    sharpe_ratio = round(float(np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)), 2)

                cumulative_returns = np.cumprod(1 + returns_array)
                running_max = np.maximum.accumulate(cumulative_returns)
                drawdown = (cumulative_returns - running_max) / running_max
                if len(drawdown) > 0:
                    max_drawdown_pct = round(float(np.min(drawdown) * 100), 2)

        # Asset-class contribution using current positions
        positions = db.query(Position).filter(Position.user_id.in_(user_ids)).all()
        total_value_positions = sum(p.market_value or 0.0 for p in positions) or 0.0

        def infer_type(pos: Position) -> str:
            meta = pos.metadata_json or {}
            candidate = (meta.get("asset_class") or meta.get("assetClass") or meta.get("class") or "").lower()
            if candidate:
                if "crypto" in candidate:
                    return "Crypto"
                if "equity" in candidate or "stock" in candidate or "etf" in candidate:
                    return "Equities"
                if "cash" in candidate or "fiat" in candidate:
                    return "Cash"

            symbol = (pos.symbol or "").upper()
            if symbol in {"USD", "USDC", "USDT", "CAD", "EUR"}:
                return "Cash"
            if symbol in {"BTC", "ETH", "SOL", "AVAX", "BNB", "LTC", "ADA"}:
                return "Crypto"
            return "Equities"

        buckets: dict = {}
        for pos in positions:
            value = float(pos.market_value or 0.0)
            asset_type = infer_type(pos)
            buckets[asset_type] = buckets.get(asset_type, 0.0) + value

        asset_performance = []
        if total_value_positions > 0:
            for asset_type, val in buckets.items():
                weight = val / total_value_positions
                contribution = round(total_return_pct * weight, 2)
                asset_performance.append({
                    "name": asset_type,
                    "return": contribution,
                    "risk": "N/A",
                })

            asset_performance = sorted(asset_performance, key=lambda x: x["return"], reverse=True)

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "N/A"
            return f"{val:+.2f}%"

        kpis = [
            {
                "title": "Alpha",
                "value": format_pct(total_return_pct),
                "subtitle": "Portfolio return over period",
                "icon": "psychology",
                "color": "text-primary",
            },
            {
                "title": "Beta",
                "value": "N/A",
                "subtitle": "Benchmark data not available",
                "icon": "ssid_chart",
                "color": "text-blue-400",
            },
            {
                "title": "Sharpe Ratio",
                "value": "N/A" if sharpe_ratio is None else f"{sharpe_ratio:.2f}",
                "subtitle": "Risk-adjusted return",
                "icon": "balance",
                "color": "text-purple-400",
            },
            {
                "title": "Max Drawdown",
                "value": format_pct(max_drawdown_pct),
                "subtitle": "Worst peak-to-trough",
                "icon": "trending_down",
                "color": "text-red-400",
            },
        ]

        return {
            "period": period,
            "kpis": kpis,
            "growth": growth,
            "monthly_returns": monthly_returns,
            "asset_performance": asset_performance,
        }
    except Exception as e:
        logger.error(f"Failed to fetch health metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch health metrics")


@router.patch("/clients/{user_id}")
async def update_client(user_id: str, payload: dict, db: Session = Depends(get_db)):
    """Update basic client fields (full_name, email, risk_profile, active)."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Client not found")

        allowed_fields = {"full_name", "email", "risk_profile", "active"}
        for key, value in payload.items():
            if key in allowed_fields:
                setattr(user, key, value)

        user.updated_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "risk_profile": user.risk_profile,
            "active": user.active,
            "updated_at": user.updated_at.isoformat() + "Z",
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update client {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update client")


@router.delete("/clients/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(user_id: str, db: Session = Depends(get_db)):
    """Delete a client and related records."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Client not found")

        # Clean up related records (best-effort), including SnapTrade access and notifications
        db.query(Position).filter(Position.user_id == user_id).delete()
        db.query(Transaction).filter(Transaction.user_id == user_id).delete()
        db.query(Connection).filter(Connection.user_id == user_id).delete()
        db.query(PortfolioSnapshot).filter(PortfolioSnapshot.user_id == user_id).delete()
        db.query(RiskProfile).filter(RiskProfile.user_id == user_id).delete()
        db.query(Alert).filter(Alert.user_id == user_id).delete()
        db.query(AlertPreference).filter(AlertPreference.user_id == user_id).delete()
        db.query(Log).filter(Log.user_id == user_id).delete()

        # Wipe SnapTrade identifiers on the user before deletion to avoid retaining secrets
        user.snaptrade_user_id = None
        user.snaptrade_token = None
        user.snaptrade_linked = False
        db.add(user)

        db.delete(user)
        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete client {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete client")


def _normalize_risk(risk: Optional[str]) -> Optional[str]:
    if not risk:
        return None
    lower = risk.lower()
    if lower in {"high", "aggressive"}:
        return "Aggressive"
    if lower in {"medium", "balanced"}:
        return "Balanced"
    if lower in {"low", "conservative"}:
        return "Conservative"
    return risk.title()


@router.get("/portfolio/metrics")
async def get_portfolio_metrics(
    risk_profile: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get overall portfolio metrics for dashboard
    
    Returns:
        Portfolio statistics including total value, 24h change, positions count, etc.
    """
    try:
        logger.info("Fetching portfolio metrics...")
        
        # Filter users by risk profile if provided
        normalized_risk = _normalize_risk(risk_profile)
        users_query = db.query(User)
        if normalized_risk:
            users_query = users_query.filter(func.lower(User.risk_profile) == normalized_risk.lower())
        user_ids = [u.id for u in users_query.all()]

        if not user_ids:
            return {
                "total_value": 0.0,
                "change_24h": 0.0,
                "change_24h_percent": 0.0,
                "total_positions": 0,
                "active_users": 0,
                "transactions_24h": 0,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        # Query database for portfolio data scoped to filtered users
        total_users = len(user_ids)
        total_positions = db.query(Position).filter(Position.user_id.in_(user_ids)).count()
        
        # Calculate total portfolio value from all user positions
        positions = db.query(Position).filter(Position.user_id.in_(user_ids)).all()
        total_value = sum(p.market_value for p in positions if p.market_value)
        
        # Calculate 24h change (simplified - would need price history)
        # TODO: Implement proper 24h change calculation with historical prices
        change_24h = 2.34  # Placeholder
        
        # Get recent transaction count
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        transactions_24h = db.query(Transaction).filter(
            Transaction.created_at >= yesterday,
            Transaction.user_id.in_(user_ids)
        ).count()
        
        return {
            "total_value": total_value,
            "change_24h": change_24h,
            "change_24h_percent": (change_24h / total_value * 100) if total_value > 0 else 0,
            "total_positions": total_positions,
            "active_users": total_users,
            "transactions_24h": transactions_24h,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch portfolio metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/positions")
async def get_portfolio_positions(
    user_id: Optional[str] = None,
    risk_profile: Optional[str] = None,
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
        logger.info(f"Fetching positions (user_id={user_id}, risk_profile={risk_profile}, limit={limit})...")

        query = db.query(Position)

        if user_id:
            query = query.filter(Position.user_id == user_id)
        elif risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            user_ids = [u.id for u in db.query(User.id).filter(func.lower(User.risk_profile) == normalized_risk.lower()).all()]
            if not user_ids:
                return []
            query = query.filter(Position.user_id.in_(user_ids))
        
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
    risk_profile: Optional[str] = None,
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
        logger.info(f"Fetching transactions (user_id={user_id}, risk_profile={risk_profile}, limit={limit})...")

        query = db.query(Transaction)

        if user_id:
            query = query.filter(Transaction.user_id == user_id)
        elif risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            user_ids = [u.id for u in db.query(User.id).filter(func.lower(User.risk_profile) == normalized_risk.lower()).all()]
            if not user_ids:
                return []
            query = query.filter(Transaction.user_id.in_(user_ids))
        
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
    risk_profile: Optional[str] = None,
    period: str = "1M",
    resolution: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Return aggregated performance across clients (or a specific client).

    - Aggregates PortfolioSnapshot totals per calendar day.
    - A client's contribution starts on their account creation date.
    - Supports common period tokens: 7D, 1M, 3M, 6M, 1Y, YTD, ALL.
    """
    try:
        logger.info(f"Fetching performance data (period={period}, user_id={user_id}, risk_profile={risk_profile})...")

        # Determine lookback window and resolution (auto: hourly for 1D/7D)
        now = datetime.now(timezone.utc)
        upper = period.upper()
        desired_resolution = (resolution or "auto").lower()
        use_hourly = desired_resolution == "hourly" or (desired_resolution == "auto" and upper in {"1D", "7D"})

        if upper == "YTD":
            start_date = datetime(now.year, 1, 1)
        else:
            days_map = {
                "1D": 1,
                "7D": 7,
                "1M": 30,
                "3M": 90,
                "6M": 180,
                "1Y": 365,
                "ALL": 5 * 365,
            }
            days = days_map.get(upper, 30)
            start_date = now - timedelta(days=days)

        # Fetch relevant users
        user_query = db.query(User)
        if user_id:
            user_query = user_query.filter(User.id == user_id)
        elif risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            user_query = user_query.filter(func.lower(User.risk_profile) == normalized_risk.lower())
        users = user_query.all()
        if not users:
            return {"period": period, "data": [], "total_return": 0, "benchmark_return": None}

        created_map = {u.id: (u.created_at or now) for u in users}
        user_ids = list(created_map.keys())

        # Pull snapshots within window
        snapshots_q = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.user_id.in_(user_ids))
        snapshots_q = snapshots_q.filter(PortfolioSnapshot.recorded_at >= start_date)
        snapshots = snapshots_q.order_by(PortfolioSnapshot.recorded_at).all()

        if not snapshots:
            return {"period": period, "data": [], "total_return": 0, "benchmark_return": None}

        # Aggregate by bucket (hourly or daily); skip data before a user's creation date
        buckets: dict = {}
        for snap in snapshots:
            created_at = created_map.get(snap.user_id, snap.recorded_at)
            if snap.recorded_at < created_at:
                continue
            if use_hourly:
                key_dt = snap.recorded_at.replace(minute=0, second=0, microsecond=0)
            else:
                key_dt = datetime.combine(snap.recorded_at.date(), datetime.min.time())
            buckets[key_dt] = buckets.get(key_dt, 0.0) + float(snap.total_value or 0.0)

        if not buckets:
            return {"period": period, "data": [], "total_return": 0, "benchmark_return": None, "benchmark_available": False, "resolution": "hourly" if use_hourly else "daily"}

        # Determine if benchmark should be shown (only when market data is available)
        system_status = db.query(SystemStatus).filter(SystemStatus.id == "system").first()
        allow_benchmark = bool(system_status and system_status.market_data_available)

        if use_hourly:
            benchmark_series = _get_sp500_intraday()
            benchmark_map = {row["timestamp"].replace(minute=0, second=0, microsecond=0): row["close"] for row in benchmark_series}
        else:
            benchmark_series = _get_sp500_history()
            benchmark_map = {row["date"]: row["close"] for row in benchmark_series}

        sorted_keys = sorted(buckets.keys())
        data = []
        last_bench = None
        for key_dt in sorted_keys:
            bench_val = None
            if allow_benchmark:
                lookup_key = key_dt if use_hourly else key_dt.date()
                bench_val = benchmark_map.get(lookup_key, last_bench)
                if bench_val is not None:
                    last_bench = bench_val

            data.append({
                "date": key_dt.isoformat() + "Z",
                "value": round(buckets[key_dt], 2),
                "benchmark": bench_val,
            })

        first_val = data[0]["value"] if data else 0
        last_val = data[-1]["value"] if data else 0
        total_return = ((last_val - first_val) / first_val * 100) if first_val else 0

        first_bench = data[0].get("benchmark") if data else None
        last_bench = data[-1].get("benchmark") if data else None
        benchmark_return = None
        if allow_benchmark and first_bench and last_bench:
            benchmark_return = ((last_bench - first_bench) / first_bench * 100)

        return {
            "period": period,
            "data": data,
            "total_return": round(total_return, 2),
            "benchmark_return": round(benchmark_return, 2) if benchmark_return is not None else None,
            "benchmark_available": allow_benchmark and any(point.get("benchmark") is not None for point in data),
            "resolution": "hourly" if use_hourly else "daily",
        }

    except Exception as e:
        logger.error(f"Failed to fetch performance data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/allocation")
async def get_portfolio_allocation(
    user_id: Optional[str] = None,
    risk_profile: Optional[str] = None,
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
        logger.info(f"Fetching allocation data (user_id={user_id}, risk_profile={risk_profile})...")
        
        query = db.query(Position)

        if user_id:
            query = query.filter(Position.user_id == user_id)
        elif risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            user_ids = [u.id for u in db.query(User.id).filter(func.lower(User.risk_profile) == normalized_risk.lower()).all()]
            if not user_ids:
                return {"by_type": [], "by_asset": [], "total_value": 0}
            query = query.filter(Position.user_id.in_(user_ids))

        positions = query.all()

        # Calculate total value
        total_value = sum(p.market_value for p in positions if p.market_value) or 1

        allocation_by_asset = []
        type_totals = {}

        def infer_type(pos: Position) -> str:
            meta = pos.metadata_json or {}
            candidate = (meta.get("asset_class") or meta.get("assetClass") or meta.get("class") or "").lower()
            if candidate:
                if "crypto" in candidate:
                    return "Crypto"
                if "equity" in candidate or "stock" in candidate or "etf" in candidate:
                    return "Equities"
                if "cash" in candidate or "fiat" in candidate:
                    return "Cash"

            symbol = (pos.symbol or "").upper()
            if symbol in {"USD", "USDC", "USDT", "CAD", "EUR"}:
                return "Cash"
            if symbol in {"BTC", "ETH", "SOL", "AVAX", "BNB", "LTC", "ADA"}:
                return "Crypto"
            # Default to equities-style bucket
            return "Equities"

        for pos in positions:
            value = pos.market_value or 0
            asset_type = infer_type(pos)

            allocation_by_asset.append({
                "symbol": pos.symbol,
                "value": value,
                "percent": round(value / total_value * 100, 2),
                "type": asset_type,
            })

            type_totals[asset_type] = type_totals.get(asset_type, 0.0) + value

        allocation_by_type_list = [
            {
                "category": t,
                "value": val,
                "percent": round(val / total_value * 100, 2)
            }
            for t, val in sorted(type_totals.items(), key=lambda item: item[1], reverse=True)
        ]

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
    risk_profile: Optional[str] = None,
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
        logger.info(f"Fetching risk metrics (method={returnMethod}, period={period}, lookback={lookback}, risk_profile={risk_profile})")
        
        # Calculate portfolio returns from snapshots
        from ..models.database import PortfolioSnapshot
        from ..core.config import get_settings
        import numpy as np
        
        config = get_settings()
        lookback_date = datetime.now(timezone.utc) - timedelta(days=lookback)
        
        # Determine which users to include based on risk profile (if provided)
        user_ids_query = db.query(User.id)
        if risk_profile:
            user_ids_query = user_ids_query.filter(func.lower(User.risk_profile) == risk_profile.lower())

        user_ids = [row[0] for row in user_ids_query.all()]
        if not user_ids:
            return {
                "sharpeRatio": None,
                "sortinoRatio": None,
                "calmarRatio": None,
                "maxDrawdown": None,
                "volatility": None,
                "returnPercent": 0,
                "message": "No users found for requested risk profile" if risk_profile else "No users found"
            }

        # Get portfolio snapshots for lookback period for selected users
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.recorded_at >= lookback_date,
            PortfolioSnapshot.user_id.in_(user_ids)
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
            "userCount": len(user_ids),
            "riskProfile": risk_profile,
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


@router.delete("/portfolio/risk-metrics")
async def delete_risk_metrics(db: Session = Depends(get_db)):
    """
    Delete all portfolio snapshots used for risk metrics (admin maintenance).
    This is intended for clearing test data.
    """
    try:
        deleted = db.query(PortfolioSnapshot).delete()
        db.commit()
        logger.warning(f"Deleted {deleted} portfolio snapshots for risk metrics reset")
        return {"deleted": deleted}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete risk metrics snapshots: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete risk metrics snapshots")


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
        
        # Re-attach user to this DB session to avoid "not persistent" errors
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Update user's risk profile
        user.risk_profile = request.risk_profile
        user.updated_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        db.refresh(user)
        
        logger.info(f"Updated risk profile for user {user.email}: {request.risk_profile}")
        
        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "risk_profile": user.risk_profile,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None
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
