"""
Portfolio Snapshot Job

Creates portfolio snapshots automatically for all users.
Performance tracking is fully automatic - no user interaction required.

Rules:
- Every 4 hours, check all users and auto-create sessions if needed
- Record snapshots for all users with positions
- Sessions are created automatically when user has portfolio data
- Chart baseline is 0% (internally tracked as $1.00 for calculations)
"""

from app.models.database import SessionLocal, User, Position, PortfolioSnapshot, PerformanceSession, BenchmarkSnapshot
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging
import uuid

logger = logging.getLogger(__name__)


async def create_snapshots():
    """
    Create portfolio snapshots for all users automatically.
    Called every 4 hours by APScheduler.
    
    Fully automatic:
    1. Auto-creates sessions for users who don't have one
    2. Records snapshots for all users with positions
    3. No manual intervention required
    """
    db = SessionLocal()
    try:
        # Get all users with positions (active portfolios)
        users_with_positions = (
            db.query(User)
            .join(Position, Position.user_id == User.id)
            .distinct()
            .all()
        )
        
        if not users_with_positions:
            logger.info("[SKIP] No users with positions found")
            return
        
        snapshot_count = 0
        session_created_count = 0
        benchmark_count = 0
        error_count = 0
        
        for user in users_with_positions:
            try:
                # Auto-create session if user doesn't have one
                session = _get_or_create_session(db, user.id)
                if session.metadata_json.get("just_created"):
                    session_created_count += 1
                    # Clear the flag
                    session.metadata_json = {k: v for k, v in session.metadata_json.items() if k != "just_created"}
                
                # Get all positions for this user
                positions = db.query(Position).filter(Position.user_id == user.id).all()
                
                if not positions:
                    continue
                
                # Calculate totals from actual positions
                total_value = sum(p.market_value or 0 for p in positions)
                crypto_value = sum(p.market_value or 0 for p in positions if _is_crypto(p))
                stocks_value = sum(p.market_value or 0 for p in positions if _is_stock(p))
                cash_value = sum(p.market_value or 0 for p in positions if _is_cash(p))
                
                # Skip if no value
                if total_value <= 0:
                    continue
                
                # Build positions and allocation snapshots (JSON)
                positions_snapshot = {}
                allocation_snapshot = {}
                
                for pos in positions:
                    positions_snapshot[pos.symbol] = {
                        "quantity": float(pos.quantity or 0),
                        "price": float(pos.price or 0),
                        "market_value": float(pos.market_value or 0),
                    }
                    
                    if total_value > 0:
                        allocation_snapshot[pos.symbol] = float(pos.market_value or 0) / total_value
                
                # Calculate return from last snapshot
                last_snapshot = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.user_id == user.id)
                    .order_by(PortfolioSnapshot.recorded_at.desc())
                    .first()
                )
                
                daily_return = 0.0
                daily_return_pct = 0.0
                
                if last_snapshot:
                    daily_return = total_value - (last_snapshot.total_value or 0)
                    if last_snapshot.total_value and last_snapshot.total_value > 0:
                        daily_return_pct = (daily_return / last_snapshot.total_value) * 100
                
                # Create snapshot record
                now = datetime.now(timezone.utc)
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
                    recorded_at=now,
                    aggregation_level="4h"
                )
                
                db.add(snapshot)
                snapshot_count += 1
                
                # Update session's last snapshot timestamp
                session.last_snapshot_at = now
                
                # Also record benchmark snapshot
                benchmark_snapshot = _create_benchmark_snapshot(db, session)
                if benchmark_snapshot:
                    db.add(benchmark_snapshot)
                    benchmark_count += 1
                
            except Exception as e:
                logger.error(f"Failed to create snapshot for user {user.id}: {str(e)}", exc_info=True)
                error_count += 1
        
        db.commit()
        logger.info(f"[OK] Snapshots: {snapshot_count} portfolio, {benchmark_count} benchmark, {session_created_count} new sessions, {error_count} errors")
        
    except Exception as e:
        logger.error(f"Failed to create portfolio snapshots: {str(e)}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def _get_or_create_session(db, user_id: str) -> PerformanceSession:
    """
    Get existing active session or auto-create one.
    Sessions are created automatically - no user action needed.
    """
    # Check for existing active session
    session = (
        db.query(PerformanceSession)
        .filter(
            PerformanceSession.user_id == user_id,
            PerformanceSession.is_active == True
        )
        .first()
    )
    
    if session:
        return session
    
    # Auto-create new session
    now = datetime.now(timezone.utc)
    benchmark_start = now - timedelta(days=30)  # 30 days of benchmark history
    
    session = PerformanceSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        is_active=True,
        baseline_value=1.0,  # Internal baseline for calculations (chart shows 0%)
        started_at=now,
        benchmark_start_date=benchmark_start,
        benchmark_ticker="SPY",
        metadata_json={
            "created_reason": "auto_created",
            "just_created": True
        }
    )
    
    db.add(session)
    db.flush()  # Get the ID
    
    # Pre-populate benchmark history
    _populate_benchmark_history(db, session)
    
    logger.info(f"Auto-created performance session for user {user_id}")
    return session


def _populate_benchmark_history(db, session: PerformanceSession) -> int:
    """Pre-populate 30 days of benchmark history when session is created."""
    try:
        from app.routers.portfolio import _get_sp500_history
        
        benchmark_data = _get_sp500_history()
        if not benchmark_data:
            return 0
        
        benchmark_start = session.benchmark_start_date
        snapshots_created = 0
        first_value = None
        
        for record in benchmark_data:
            record_date = record.get("date")
            if isinstance(record_date, str):
                record_date = datetime.fromisoformat(record_date.replace("Z", "+00:00"))
            elif not isinstance(record_date, datetime):
                # It might be a date object
                if hasattr(record_date, 'year'):
                    record_date = datetime.combine(record_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                else:
                    continue
            
            if record_date.tzinfo is None:
                record_date = record_date.replace(tzinfo=timezone.utc)
            
            if benchmark_start and record_date < benchmark_start:
                continue
            
            value = record.get("close", 0)
            if value <= 0:
                continue
            
            if first_value is None:
                first_value = value
            
            # Return percentage from start (0% baseline)
            return_pct = ((value - first_value) / first_value * 100) if first_value else 0
            
            snapshot = BenchmarkSnapshot(
                id=str(uuid.uuid4()),
                session_id=session.id,
                ticker=session.benchmark_ticker,
                value=value,
                return_pct=return_pct,
                recorded_at=record_date,
                source="twelve_data"
            )
            
            db.add(snapshot)
            snapshots_created += 1
        
        return snapshots_created
        
    except Exception as e:
        logger.error(f"Failed to populate benchmark history: {str(e)}")
        return 0


def _is_crypto(position: Position) -> bool:
    """Check if position is a crypto asset"""
    crypto_symbols = {'BTC', 'ETH', 'SOL', 'ADA', 'DOT', 'AVAX', 'MATIC', 'LINK', 'UNI', 'ATOM'}
    symbol = (position.symbol or "").upper()
    return symbol in crypto_symbols or symbol.endswith('USD') or symbol.endswith('CAD')


def _is_stock(position: Position) -> bool:
    """Check if position is a stock/equity"""
    cash_symbols = {'CAD', 'USD', 'CASH', 'MONEY'}
    symbol = (position.symbol or "").upper()
    return not _is_crypto(position) and symbol not in cash_symbols


def _is_cash(position: Position) -> bool:
    """Check if position is cash"""
    cash_symbols = {'CAD', 'USD', 'CASH', 'MONEY'}
    symbol = (position.symbol or "").upper()
    return symbol in cash_symbols


def _create_benchmark_snapshot(db, session: PerformanceSession) -> Optional[BenchmarkSnapshot]:
    """Create a benchmark snapshot for the current time."""
    try:
        from app.routers.portfolio import _get_sp500_history
        
        benchmark_data = _get_sp500_history()
        if not benchmark_data:
            return None
        
        latest = benchmark_data[0] if benchmark_data else None
        if not latest:
            return None
        
        value = latest.get("close", 0)
        if value <= 0:
            return None
        
        # Get the first benchmark snapshot to calculate return from 0%
        first_benchmark = (
            db.query(BenchmarkSnapshot)
            .filter(BenchmarkSnapshot.session_id == session.id)
            .order_by(BenchmarkSnapshot.recorded_at.asc())
            .first()
        )
        
        first_value = first_benchmark.value if first_benchmark else value
        return_pct = ((value - first_value) / first_value * 100) if first_value else 0
        
        return BenchmarkSnapshot(
            id=str(uuid.uuid4()),
            session_id=session.id,
            ticker=session.benchmark_ticker,
            value=value,
            return_pct=return_pct,
            recorded_at=datetime.now(timezone.utc),
            source="twelve_data"
        )
        
    except Exception as e:
        logger.error(f"Failed to create benchmark snapshot: {str(e)}")
        return None
