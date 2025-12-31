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
import requests

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
    PerformanceSession,
    BenchmarkSnapshot,
)
from ..routers.auth import get_current_user, oauth2_scheme
from ..core.config import get_settings
from ..services.snaptrade_integration import get_snaptrade_client
from ..services.performance_session import PerformanceSessionService
from ..services.market_data import get_live_crypto_prices, get_live_equity_price
from ..core.currency import convert_to_cad

settings = get_settings()
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

# List of S&P 500 tickers to try (fallbacks if main one fails)
SP500_TICKERS = ["SPY", "^GSPC", "SPX"]


def _fetch_benchmark_from_twelve_data(symbol: str = "SPY", outputsize: int = 1260) -> List[dict]:
    """Fetch S&P 500 data from Twelve Data API.
    
    Free tier: 8 calls/minute, 800 calls/day
    Sign up at twelvedata.com for an API key.
    """
    try:
        api_key = settings.TWELVE_DATA_API_KEY
        if not api_key:
            logger.warning("TWELVE_DATA_API_KEY not configured - cannot fetch benchmark data")
            return []
        
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": outputsize,  # ~5 years of trading days
            "format": "JSON",
            "apikey": api_key
        }
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if "values" in data and data["values"]:
                records = []
                for item in data["values"]:
                    try:
                        day = datetime.strptime(item["datetime"], "%Y-%m-%d").date()
                        close_val = float(item["close"])
                        records.append({"date": day, "close": close_val})
                    except (ValueError, KeyError):
                        continue
                if records:
                    # Reverse to chronological order (API returns newest first)
                    records.reverse()
                    logger.info(f"Fetched {len(records)} benchmark data points from Twelve Data API ({symbol})")
                    return records
            else:
                logger.warning(f"Twelve Data API returned no values for {symbol}: {data.get('message', 'No message')}")
        else:
            logger.warning(f"Twelve Data API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Twelve Data API failed for {symbol}: {e}")
    
    return []


def _fetch_benchmark_intraday_from_twelve_data(symbol: str = "SPY", outputsize: int = 50) -> List[dict]:
    """Fetch intraday S&P 500 data from Twelve Data API (hourly intervals)."""
    try:
        api_key = settings.TWELVE_DATA_API_KEY
        if not api_key:
            logger.warning("TWELVE_DATA_API_KEY not configured - cannot fetch intraday benchmark data")
            return []
        
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": "1h",
            "outputsize": outputsize,  # Last ~50 hours
            "format": "JSON",
            "apikey": api_key
        }
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if "values" in data and data["values"]:
                records = []
                for item in data["values"]:
                    try:
                        ts = datetime.strptime(item["datetime"], "%Y-%m-%d %H:%M:%S")
                        ts = ts.replace(tzinfo=timezone.utc)
                        close_val = float(item["close"])
                        records.append({"timestamp": ts, "close": close_val})
                    except (ValueError, KeyError):
                        continue
                if records:
                    # Reverse to chronological order (API returns newest first)
                    records.reverse()
                    logger.info(f"Fetched {len(records)} intraday benchmark data points from Twelve Data API ({symbol})")
                    return records
            else:
                logger.warning(f"Twelve Data API returned no intraday values for {symbol}")
        else:
            logger.warning(f"Twelve Data API intraday returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Twelve Data API intraday failed for {symbol}: {e}")
    
    return []


def _fetch_sp500_history() -> List[dict]:
    """Fetch last 5y of SP500 daily closes using Twelve Data API."""
    for ticker in SP500_TICKERS:
        logger.info(f"Attempting to fetch benchmark data from Twelve Data API ({ticker})")
        records = _fetch_benchmark_from_twelve_data(symbol=ticker)
        if records:
            return records
        logger.warning(f"Failed to get benchmark data for {ticker}, trying next...")
    
    logger.error("All benchmark tickers failed - no real market data available")
    return []


def _fetch_sp500_intraday(days: int = 7) -> List[dict]:
    """Fetch intraday S&P 500 (hourly) closes using Twelve Data API."""
    outputsize = min(days * 7, 50)  # Twelve Data free tier limit
    
    for ticker in SP500_TICKERS:
        logger.info(f"Attempting to fetch intraday benchmark from Twelve Data API ({ticker})")
        records = _fetch_benchmark_intraday_from_twelve_data(symbol=ticker, outputsize=outputsize)
        if records:
            return records
        logger.warning(f"Failed to get intraday benchmark for {ticker}, trying next...")
    
    logger.error("All intraday benchmark sources failed - no real market data available")
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


# ============================================================================
# PERFORMANCE SESSION MANAGEMENT
# Performance tracking starts only when a session is opened.
# ============================================================================

class StartSessionRequest(BaseModel):
    benchmark_ticker: str = "SPY"


@router.post("/performance/session/start")
async def start_performance_session(
    request: StartSessionRequest = StartSessionRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Start a new performance tracking session.
    
    - Initializes portfolio performance at $1.00 baseline (= 0% on charts)
    - Pre-populates benchmark data for 30 days prior to today
    - Returns the active session
    
    If a session is already active, returns the existing session.
    """
    try:
        service = PerformanceSessionService(db)
        session = service.start_session(
            user_id=current_user.id,
            benchmark_ticker=request.benchmark_ticker,
            fetch_benchmark_data=True
        )
        
        return {
            "status": "success",
            "message": "Performance session started",
            "session": {
                "id": session.id,
                "user_id": session.user_id,
                "is_active": session.is_active,
                "baseline_value": session.baseline_value,
                "started_at": session.started_at.isoformat(),
                "benchmark_ticker": session.benchmark_ticker,
                "benchmark_start_date": session.benchmark_start_date.isoformat() if session.benchmark_start_date else None
            }
        }
    except Exception as e:
        logger.error(f"Failed to start performance session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/performance/session/stop")
async def stop_performance_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Stop the active performance tracking session.
    
    - Pauses data recording
    - Does NOT delete any existing data
    - Session can be resumed later
    """
    try:
        service = PerformanceSessionService(db)
        session = service.stop_session(current_user.id)
        
        if not session:
            raise HTTPException(status_code=404, detail="No active session found")
        
        return {
            "status": "success",
            "message": "Performance session stopped",
            "session": {
                "id": session.id,
                "is_active": session.is_active,
                "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop performance session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/performance/session/resume")
async def resume_performance_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Resume the most recently stopped performance session.
    
    - Reactivates the session
    - Continues from last stored snapshot (no reset of baseline)
    - Appends only new data going forward
    """
    try:
        service = PerformanceSessionService(db)
        session = service.resume_session(current_user.id)
        
        if not session:
            raise HTTPException(status_code=404, detail="No stopped session found to resume")
        
        return {
            "status": "success",
            "message": "Performance session resumed",
            "session": {
                "id": session.id,
                "is_active": session.is_active,
                "started_at": session.started_at.isoformat(),
                "last_snapshot_at": session.last_snapshot_at.isoformat() if session.last_snapshot_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resume performance session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/performance/session")
async def get_performance_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the current performance session status and data.
    
    Returns session info and performance data if a session exists.
    """
    try:
        service = PerformanceSessionService(db)
        session = service.get_active_session(current_user.id)
        
        if not session:
            # Check for any stopped sessions
            stopped_session = (
                db.query(PerformanceSession)
                .filter(
                    PerformanceSession.user_id == current_user.id,
                    PerformanceSession.is_active == False
                )
                .order_by(PerformanceSession.stopped_at.desc())
                .first()
            )
            
            return {
                "status": "no_active_session",
                "has_stopped_session": stopped_session is not None,
                "stopped_session_id": stopped_session.id if stopped_session else None
            }
        
        # Get performance data
        perf_data = service.get_session_performance(session.id)
        
        return {
            "status": "active",
            "session": {
                "id": session.id,
                "is_active": session.is_active,
                "baseline_value": session.baseline_value,
                "started_at": session.started_at.isoformat(),
                "benchmark_ticker": session.benchmark_ticker
            },
            "performance": perf_data
        }
    except Exception as e:
        logger.error(f"Failed to get performance session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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
            start_date = datetime(now.year, 1, 1, tzinfo=timezone.utc)
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

        def _ensure_aware(dt):
            """Ensure datetime is timezone-aware (UTC)."""
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

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

        created_map = {u.id: _ensure_aware(u.created_at) or now for u in users}
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
            snap_recorded = _ensure_aware(snap.recorded_at)
            created_at = created_map.get(snap.user_id, snap_recorded)
            if snap_recorded < created_at:
                continue
            day = snap_recorded.date()
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

            dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
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
    """Update basic client fields (full_name, email, risk_profile, active).
    
    When setting active=False (suspension), also deletes SnapTrade users to stop billing.
    """
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Client not found")

        # Check if this is a suspension (active being set to False)
        is_suspension = payload.get("active") is False and user.active is not False

        allowed_fields = {"full_name", "email", "risk_profile", "active"}
        for key, value in payload.items():
            if key in allowed_fields:
                setattr(user, key, value)

        # If suspending, delete SnapTrade users to stop billing
        if is_suspension:
            connections = db.query(Connection).filter(Connection.user_id == user_id).all()
            snaptrade_user_ids: set[str] = set()
            for conn in connections:
                if conn.snaptrade_user_id:
                    snaptrade_user_ids.add(conn.snaptrade_user_id)

            if snaptrade_user_ids:
                try:
                    snaptrade = get_snaptrade_client()
                    for sid in snaptrade_user_ids:
                        try:
                            snaptrade.authentication.delete_snap_trade_user(user_id=sid)
                            logger.info(f"Deleted SnapTrade user {sid} during suspension of client {user_id}")
                        except Exception as exc:
                            logger.warning(f"Failed to delete SnapTrade user {sid}: {exc}")
                except Exception as exc:
                    logger.warning(f"SnapTrade client init failed during suspension: {exc}")

            # Delete local connections and reset SnapTrade link status
            for conn in connections:
                db.delete(conn)
            user.snaptrade_linked = False

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
    """Delete a client and related records, including SnapTrade users."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Client not found")

        # Delete SnapTrade users first to stop billing
        connections = db.query(Connection).filter(Connection.user_id == user_id).all()
        snaptrade_user_ids: set[str] = set()
        for conn in connections:
            if conn.snaptrade_user_id:
                snaptrade_user_ids.add(conn.snaptrade_user_id)

        if snaptrade_user_ids:
            try:
                snaptrade = get_snaptrade_client()
                for sid in snaptrade_user_ids:
                    try:
                        snaptrade.authentication.delete_snap_trade_user(user_id=sid)
                        logger.info(f"Deleted SnapTrade user {sid} for client {user_id}")
                    except Exception as exc:
                        logger.warning(f"Failed to delete SnapTrade user {sid}: {exc}")
                        # Continue with deletion even if SnapTrade cleanup fails
            except Exception as exc:
                logger.warning(f"SnapTrade client init failed during client delete: {exc}")

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


@router.get("/balance-history")
async def get_balance_history(
    category: str = "all",
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """
    Get historical balance data for the current user's account.
    
    Returns balance history from account creation to now, suitable for line charts.
    Includes the current live value as the latest data point.
    
    Args:
        category: Filter by asset category - 'all', 'crypto', 'equity', or 'cash'
    
    Returns:
        List of {timestamp, value} points for chart rendering
    """
    try:
        # Get current user from token
        user = get_current_user(token, db)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        # Fetch all snapshots for this user since account creation
        snapshots = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == user.id)
            .order_by(PortfolioSnapshot.recorded_at.asc())
            .all()
        )
        
        # Build response based on category
        category_lower = category.lower().strip()
        history = []
        
        for snap in snapshots:
            if category_lower == "all":
                value = float(snap.total_value or 0.0)
            elif category_lower == "crypto":
                value = float(snap.crypto_value or 0.0)
            elif category_lower == "equity" or category_lower == "equities":
                value = float(snap.stocks_value or 0.0)
            elif category_lower == "cash":
                value = float(snap.cash_value or 0.0)
            else:
                value = float(snap.total_value or 0.0)
            
            history.append({
                "timestamp": snap.recorded_at.isoformat() if snap.recorded_at else None,
                "value": round(value, 2),
            })
        
        # If no snapshots, return empty with account creation date
        if not history and user.created_at:
            history.append({
                "timestamp": user.created_at.isoformat(),
                "value": 0.0,
            })
        
        # Add current live value as the latest data point
        # Fetch current positions to get live totals
        try:
            positions = db.query(Position).filter(Position.user_id == user.id).all()
            current_total = 0.0
            current_crypto = 0.0
            current_equity = 0.0
            current_cash = 0.0
            
            for pos in positions:
                val = float(pos.market_value or 0.0)
                metadata = pos.metadata_json or {}
                asset_class = metadata.get('asset_class', '').lower()
                
                current_total += val
                if asset_class == 'crypto':
                    current_crypto += val
                elif asset_class == 'equity':
                    current_equity += val
                elif asset_class == 'cash':
                    current_cash += val
            
            # Select the appropriate current value based on category
            if category_lower == "all":
                current_value = current_total
            elif category_lower == "crypto":
                current_value = current_crypto
            elif category_lower == "equity" or category_lower == "equities":
                current_value = current_equity
            elif category_lower == "cash":
                current_value = current_cash
            else:
                current_value = current_total
            
            # Add current value with current timestamp (only if different from last)
            now = datetime.now(timezone.utc)
            if current_value > 0:
                # Only add if it's meaningfully different from the last point or enough time has passed
                if not history or (history[-1]["value"] != round(current_value, 2)):
                    history.append({
                        "timestamp": now.isoformat(),
                        "value": round(current_value, 2),
                    })
        except Exception as e:
            logger.warning(f"Failed to add live value to balance history: {e}")
        
        return {
            "category": category_lower,
            "user_id": user.id,
            "account_created": user.created_at.isoformat() if user.created_at else None,
            "data_points": len(history),
            "history": history,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch balance history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
        
        # Calculate total cost basis and P&L from positions
        total_cost_basis = 0.0
        total_current_value = 0.0
        for p in positions:
            if p.cost_basis and p.cost_basis > 0 and p.quantity:
                total_cost_basis += p.cost_basis * p.quantity
            if p.market_value:
                total_current_value += p.market_value
        
        # Calculate actual P&L (change) based on cost basis
        if total_cost_basis > 0:
            change_24h = total_current_value - total_cost_basis
            change_24h_percent = ((total_current_value - total_cost_basis) / total_cost_basis) * 100
        else:
            change_24h = 0.0
            change_24h_percent = 0.0
        
        # Get recent transaction count
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        transactions_24h = db.query(Transaction).filter(
            Transaction.created_at >= yesterday,
            Transaction.user_id.in_(user_ids)
        ).count()
        
        return {
            "total_value": total_value,
            "change_24h": change_24h,
            "change_24h_percent": change_24h_percent,
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
    Get portfolio positions/assets with live prices
    
    Args:
        user_id: Optional user ID to filter by
        limit: Maximum number of positions to return
        
    Returns:
        List of portfolio positions with current values and live prices
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
        
        # Collect crypto symbols for live price fetching
        crypto_symbols = []
        for pos in positions:
            metadata = pos.metadata_json or {}
            asset_class = metadata.get('asset_class', '').lower()
            broker = metadata.get('broker', '').lower()
            
            # Check if it's a crypto position
            if asset_class == 'crypto' or broker == 'kraken':
                # Skip stablecoins and fiat
                symbol_upper = pos.symbol.upper()
                if symbol_upper not in {"USDC", "USDT", "DAI", "USD", "CAD", "EUR", "GBP"}:
                    crypto_symbols.append(pos.symbol)
        
        # Fetch live prices for crypto assets (prices are in CAD)
        live_prices_cad = {}
        if crypto_symbols:
            try:
                live_prices_cad = get_live_crypto_prices(crypto_symbols)
                logger.info(f"Fetched live CAD prices for {len(live_prices_cad)} crypto assets")
            except Exception as e:
                logger.warning(f"Failed to fetch live crypto prices: {e}")
        
        result = []
        for pos in positions:
            metadata = pos.metadata_json or {}
            asset_class = metadata.get('asset_class', '').lower()
            broker = metadata.get('broker', '').lower()
            
            # Use live price if available (for crypto)
            current_price = pos.price
            market_value = pos.market_value
            symbol_upper = pos.symbol.upper()
            
            if symbol_upper in live_prices_cad:
                # Live price is already in CAD from CCXT (using CAD trading pairs)
                live_price_cad = live_prices_cad[symbol_upper]
                current_price = live_price_cad
                market_value = pos.quantity * current_price
                logger.info(f"Using live price for {pos.symbol}: ${current_price} CAD")
            
            # Calculate change percentage from cost basis using live price
            change_pct = 0.0
            if pos.cost_basis and pos.cost_basis > 0 and current_price:
                change_pct = ((current_price - pos.cost_basis) / pos.cost_basis) * 100
            
            # Format last order time as ISO string if present
            last_order_time_str = None
            if pos.last_order_time:
                last_order_time_str = pos.last_order_time.isoformat() if hasattr(pos.last_order_time, 'isoformat') else str(pos.last_order_time)
            
            result.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "name": metadata.get('name', pos.symbol),
                "type": "asset",
                "balance": pos.quantity,
                "price": current_price,
                "value": market_value,
                "cost_basis": pos.cost_basis,
                "change_24h": change_pct,  # Change from cost basis using live price
                "last_order_time": last_order_time_str,
                "last_order_side": pos.last_order_side or "HOLD",
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
        risk_profile: Optional risk profile to filter by
        limit: Maximum number of transactions to return
        
    Returns:
        List of recent transactions with user info for admin views
    """
    try:
        logger.info(f"Fetching transactions (user_id={user_id}, risk_profile={risk_profile}, limit={limit})...")

        query = db.query(Transaction, User).join(User, Transaction.user_id == User.id)

        if user_id:
            query = query.filter(Transaction.user_id == user_id)
        elif risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            query = query.filter(func.lower(User.risk_profile) == normalized_risk.lower())
        
        transactions = query.order_by(Transaction.created_at.desc()).limit(limit).all()
        
        result = []
        for txn, user in transactions:
            result.append({
                "id": txn.id,
                "asset": txn.symbol,
                "type": txn.side,
                "amount": txn.quantity,
                "price": txn.price,
                "total": txn.quantity * txn.price,
                "timestamp": txn.executed_at.isoformat() + "Z" if txn.executed_at else txn.created_at.isoformat() + "Z",
                "status": txn.status or "pending",
                "user_id": txn.user_id,
                "user_name": user.full_name or user.email.split('@')[0],
                "user_email": user.email,
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
            start_date = datetime(now.year, 1, 1, tzinfo=timezone.utc)
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

        def _ensure_tz_aware(dt):
            """Ensure datetime is timezone-aware (UTC)."""
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

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

        created_map = {u.id: _ensure_tz_aware(u.created_at) or now for u in users}
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
            snap_recorded = _ensure_tz_aware(snap.recorded_at)
            created_at = created_map.get(snap.user_id, snap_recorded)
            if snap_recorded < created_at:
                continue
            if use_hourly:
                key_dt = snap_recorded.replace(minute=0, second=0, microsecond=0)
            else:
                key_dt = datetime.combine(snap_recorded.date(), datetime.min.time(), tzinfo=timezone.utc)
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


@router.get("/portfolio/live-returns")
async def get_live_portfolio_returns(
    risk_profile: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Return live portfolio returns starting from 0%.
    
    Calculates cumulative return as if investing $1 at session start.
    Returns percentage return data for continuous live chart updates.
    Includes current live point computed from latest Position market values.
    """
    try:
        now = datetime.now(timezone.utc)
        
        # Fetch relevant users
        user_query = db.query(User)
        if risk_profile:
            normalized_risk = _normalize_risk(risk_profile)
            user_query = user_query.filter(func.lower(User.risk_profile) == normalized_risk.lower())
        users = user_query.all()
        
        if not users:
            return {
                "data": [{"timestamp": now.isoformat() + "Z", "return_pct": 0.0}],
                "current_return": 0.0,
                "base_value": 1.0,
                "current_value": 1.0,
            }
        
        def _ensure_tz(dt):
            """Ensure datetime is timezone-aware (UTC)."""
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        
        user_ids = [u.id for u in users]
        created_map = {u.id: _ensure_tz(u.created_at) or now for u in users}
        
        # Get the earliest user creation date as the "session start"
        earliest_created = min(created_map.values())
        
        # Pull all snapshots from session start
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.user_id.in_(user_ids),
            PortfolioSnapshot.recorded_at >= earliest_created
        ).order_by(PortfolioSnapshot.recorded_at).all()
        
        # Aggregate by timestamp (hourly buckets for granularity)
        buckets: dict = {}
        for snap in snapshots:
            snap_recorded = _ensure_tz(snap.recorded_at)
            created_at = created_map.get(snap.user_id, snap_recorded)
            if snap_recorded < created_at:
                continue
            # Use hourly buckets for finer granularity
            key_dt = snap_recorded.replace(minute=0, second=0, microsecond=0)
            buckets[key_dt] = buckets.get(key_dt, 0.0) + float(snap.total_value or 0.0)
        
        # Get current live value from Position table
        current_total = db.query(func.coalesce(func.sum(Position.market_value), 0)).filter(
            Position.user_id.in_(user_ids)
        ).scalar() or 0.0
        
        # Add current timestamp bucket
        current_bucket = now.replace(minute=0, second=0, microsecond=0)
        if current_bucket not in buckets or current_total > 0:
            buckets[current_bucket] = current_total
        
        if not buckets:
            return {
                "data": [{"timestamp": now.isoformat() + "Z", "return_pct": 0.0}],
                "current_return": 0.0,
                "base_value": 1.0,
                "current_value": 1.0,
            }
        
        sorted_keys = sorted(buckets.keys())
        base_value = buckets[sorted_keys[0]] if buckets[sorted_keys[0]] > 0 else 1.0
        
        # Convert to percentage returns (starting from 0%)
        # This is equivalent to tracking growth of $1 invested
        data = []
        for key_dt in sorted_keys:
            value = buckets[key_dt]
            # Calculate cumulative return percentage
            return_pct = ((value - base_value) / base_value) * 100 if base_value > 0 else 0.0
            data.append({
                "timestamp": key_dt.isoformat() + "Z",
                "return_pct": round(return_pct, 4),
                "value": round(value, 2),
            })
        
        # Ensure we have a "now" point with current Position values
        last_point = data[-1] if data else None
        current_return = ((current_total - base_value) / base_value) * 100 if base_value > 0 else 0.0
        
        # Add live point if it's different from last historical point
        if last_point and abs(current_return - last_point["return_pct"]) > 0.001:
            data.append({
                "timestamp": now.isoformat() + "Z",
                "return_pct": round(current_return, 4),
                "value": round(current_total, 2),
                "is_live": True,
            })
        
        return {
            "data": data,
            "current_return": round(current_return, 4),
            "base_value": round(base_value, 2),
            "current_value": round(current_total, 2),
            "session_start": earliest_created.isoformat() + "Z",
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch live returns: {str(e)}")
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
        period: Return period (1D, 1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y) - determines the date range
        lookback: Number of days for volatility/risk calculation (30, 90, 365) - fallback if period not specified
        risk_profile: Optional filter by risk profile
        
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
        now = datetime.now(timezone.utc)
        
        # Determine lookback window from period parameter
        period_upper = period.upper()
        if period_upper == "YTD":
            lookback_date = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        elif period_upper == "1D":
            lookback_date = now - timedelta(days=1)
        elif period_upper == "1W":
            lookback_date = now - timedelta(days=7)
        elif period_upper == "1M":
            lookback_date = now - timedelta(days=30)
        elif period_upper == "3M":
            lookback_date = now - timedelta(days=90)
        elif period_upper == "6M":
            lookback_date = now - timedelta(days=180)
        elif period_upper == "1Y":
            lookback_date = now - timedelta(days=365)
        elif period_upper == "3Y":
            lookback_date = now - timedelta(days=365 * 3)
        elif period_upper == "5Y":
            lookback_date = now - timedelta(days=365 * 5)
        elif period_upper == "ALL":
            lookback_date = now - timedelta(days=365 * 10)  # 10 years max
        else:
            # Fallback to lookback parameter
            lookback_date = now - timedelta(days=lookback)
        
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
                "period": period,
                "message": "No users found for requested risk profile" if risk_profile else "No users found"
            }

        # Get portfolio snapshots for the determined period for selected users
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
                "period": period,
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
                "period": period,
                "message": "Insufficient data for metrics"
            }
        
        returns_array = np.array(returns)
        
        # Calculate metrics for the specified period
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
            "period": period,
            "lookbackDays": (now - lookback_date).days,
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
