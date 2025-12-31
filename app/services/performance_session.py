"""
Performance Session Management Service

Handles the lifecycle of performance tracking sessions:
- Starting new sessions (initializes at $1.00 baseline = 0%)
- Stopping sessions (pauses data recording)
- Resuming sessions (continues from last snapshot)
- Pre-populating benchmark data (30 days prior to session start)
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.database import (
    PerformanceSession, 
    BenchmarkSnapshot, 
    PortfolioSnapshot,
    User,
    Position
)

logger = logging.getLogger(__name__)


class PerformanceSessionService:
    """Service for managing performance tracking sessions"""
    
    BENCHMARK_LOOKBACK_DAYS = 30  # Benchmark data starts 30 days before portfolio
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_active_session(self, user_id: str) -> Optional[PerformanceSession]:
        """Get the currently active performance session for a user"""
        return (
            self.db.query(PerformanceSession)
            .filter(
                PerformanceSession.user_id == user_id,
                PerformanceSession.is_active == True
            )
            .first()
        )
    
    def get_session_by_id(self, session_id: str) -> Optional[PerformanceSession]:
        """Get a session by its ID"""
        return self.db.query(PerformanceSession).filter(PerformanceSession.id == session_id).first()
    
    def start_session(
        self, 
        user_id: str, 
        benchmark_ticker: str = "SPY",
        fetch_benchmark_data: bool = True
    ) -> PerformanceSession:
        """
        Start a new performance tracking session.
        
        - If an active session exists, returns it (no duplicate sessions)
        - Initializes portfolio performance at $1.00 baseline (= 0% on charts)
        - Pre-populates benchmark data 30 days prior to today
        
        Args:
            user_id: The user ID to start the session for
            benchmark_ticker: The benchmark ticker symbol (default: SPY)
            fetch_benchmark_data: Whether to fetch and store benchmark history
            
        Returns:
            The active PerformanceSession
        """
        # Check if there's already an active session
        existing = self.get_active_session(user_id)
        if existing:
            logger.info(f"Active session already exists for user {user_id}, returning existing session")
            return existing
        
        now = datetime.now(timezone.utc)
        benchmark_start = now - timedelta(days=self.BENCHMARK_LOOKBACK_DAYS)
        
        # Create new session with $1.00 baseline
        session = PerformanceSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            is_active=True,
            baseline_value=1.0,  # $1.00 = 0% on charts
            started_at=now,
            benchmark_start_date=benchmark_start,
            benchmark_ticker=benchmark_ticker,
            metadata_json={
                "created_reason": "manual_start",
                "benchmark_lookback_days": self.BENCHMARK_LOOKBACK_DAYS
            }
        )
        
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        
        logger.info(f"Started new performance session {session.id} for user {user_id}")
        
        # Create initial portfolio snapshot at $1.00 baseline
        self._create_initial_snapshot(session)
        
        # Pre-populate benchmark data (30 days prior)
        if fetch_benchmark_data:
            self._populate_benchmark_history(session)
        
        return session
    
    def stop_session(self, user_id: str) -> Optional[PerformanceSession]:
        """
        Stop the active performance session.
        
        - Marks session as inactive
        - Records stop timestamp
        - Does NOT delete any data
        
        Args:
            user_id: The user ID to stop the session for
            
        Returns:
            The stopped session, or None if no active session exists
        """
        session = self.get_active_session(user_id)
        if not session:
            logger.warning(f"No active session found for user {user_id}")
            return None
        
        session.is_active = False
        session.stopped_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(session)
        
        logger.info(f"Stopped performance session {session.id} for user {user_id}")
        return session
    
    def resume_session(self, user_id: str) -> Optional[PerformanceSession]:
        """
        Resume the most recently stopped session.
        
        - Reactivates the session
        - Continues from last stored snapshot (no reset)
        - Appends only missing data
        
        Args:
            user_id: The user ID to resume the session for
            
        Returns:
            The resumed session, or None if no stopped session exists
        """
        # Find the most recently stopped session
        session = (
            self.db.query(PerformanceSession)
            .filter(
                PerformanceSession.user_id == user_id,
                PerformanceSession.is_active == False
            )
            .order_by(PerformanceSession.stopped_at.desc())
            .first()
        )
        
        if not session:
            logger.warning(f"No stopped session found for user {user_id}")
            return None
        
        session.is_active = True
        session.stopped_at = None
        session.metadata_json = {
            **session.metadata_json,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "resume_count": session.metadata_json.get("resume_count", 0) + 1
        }
        
        self.db.commit()
        self.db.refresh(session)
        
        logger.info(f"Resumed performance session {session.id} for user {user_id}")
        return session
    
    def _create_initial_snapshot(self, session: PerformanceSession) -> None:
        """
        Create the initial portfolio snapshot at $1.00 baseline.
        This corresponds to 0% return on charts.
        """
        initial_snapshot = PortfolioSnapshot(
            id=str(uuid.uuid4()),
            user_id=session.user_id,
            total_value=session.baseline_value,  # $1.00
            crypto_value=0.0,
            stocks_value=0.0,
            cash_value=session.baseline_value,  # Start as "cash"
            daily_return=0.0,
            daily_return_pct=0.0,
            positions_snapshot={},
            allocation_snapshot={"cash": 1.0},
            recorded_at=session.started_at,
            aggregation_level="session_start"
        )
        
        self.db.add(initial_snapshot)
        self.db.commit()
        
        logger.info(f"Created initial $1.00 baseline snapshot for session {session.id}")
    
    def _populate_benchmark_history(self, session: PerformanceSession) -> int:
        """
        Pre-populate benchmark data for 30 days prior to session start.
        
        Returns:
            Number of benchmark snapshots created
        """
        from app.routers.portfolio import _get_sp500_history
        
        try:
            # Fetch historical benchmark data
            benchmark_data = _get_sp500_history()
            
            if not benchmark_data:
                logger.warning("No benchmark data available from API")
                return 0
            
            # Filter to last 30 days and create snapshots
            benchmark_start = session.benchmark_start_date
            snapshots_created = 0
            
            # Get the first benchmark value for return calculations
            first_value = None
            
            for record in benchmark_data:
                record_date = record.get("date")
                if isinstance(record_date, str):
                    record_date = datetime.fromisoformat(record_date.replace("Z", "+00:00"))
                elif not isinstance(record_date, datetime):
                    continue
                
                # Ensure timezone awareness
                if record_date.tzinfo is None:
                    record_date = record_date.replace(tzinfo=timezone.utc)
                
                # Only include data from benchmark start date onwards
                if record_date < benchmark_start:
                    continue
                
                value = record.get("close", 0)
                if value <= 0:
                    continue
                
                # Track first value for return calculation
                if first_value is None:
                    first_value = value
                
                # Calculate return from benchmark start
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
                
                self.db.add(snapshot)
                snapshots_created += 1
            
            self.db.commit()
            logger.info(f"Created {snapshots_created} benchmark snapshots for session {session.id}")
            return snapshots_created
            
        except Exception as e:
            logger.error(f"Failed to populate benchmark history: {str(e)}")
            self.db.rollback()
            return 0
    
    def should_record_snapshot(self, user_id: str) -> bool:
        """
        Check if we should record a portfolio snapshot for a user.
        Only records if there's an active session.
        """
        session = self.get_active_session(user_id)
        return session is not None and session.is_active
    
    def get_session_performance(
        self, 
        session_id: str
    ) -> Dict[str, Any]:
        """
        Get performance data for a specific session.
        
        Returns:
            Dict with portfolio and benchmark performance data
        """
        session = self.get_session_by_id(session_id)
        if not session:
            return {"error": "Session not found"}
        
        # Get portfolio snapshots for this session (after session start)
        portfolio_snapshots = (
            self.db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.user_id == session.user_id,
                PortfolioSnapshot.recorded_at >= session.started_at
            )
            .order_by(PortfolioSnapshot.recorded_at)
            .all()
        )
        
        # Get benchmark snapshots
        benchmark_snapshots = (
            self.db.query(BenchmarkSnapshot)
            .filter(BenchmarkSnapshot.session_id == session_id)
            .order_by(BenchmarkSnapshot.recorded_at)
            .all()
        )
        
        # Calculate returns from baseline
        baseline = session.baseline_value
        portfolio_data = []
        
        for snap in portfolio_snapshots:
            return_pct = ((snap.total_value - baseline) / baseline * 100) if baseline else 0
            portfolio_data.append({
                "date": snap.recorded_at.isoformat(),
                "value": snap.total_value,
                "return_pct": round(return_pct, 2)
            })
        
        benchmark_data = []
        for snap in benchmark_snapshots:
            benchmark_data.append({
                "date": snap.recorded_at.isoformat(),
                "value": snap.value,
                "return_pct": round(snap.return_pct, 2)
            })
        
        return {
            "session_id": session.id,
            "user_id": session.user_id,
            "is_active": session.is_active,
            "started_at": session.started_at.isoformat(),
            "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
            "baseline_value": baseline,
            "benchmark_ticker": session.benchmark_ticker,
            "portfolio": portfolio_data,
            "benchmark": benchmark_data,
            "total_return": portfolio_data[-1]["return_pct"] if portfolio_data else 0,
            "benchmark_return": benchmark_data[-1]["return_pct"] if benchmark_data else 0
        }


def get_or_create_session_for_user(db: Session, user_id: str) -> PerformanceSession:
    """
    Helper function to get or create an active session for a user.
    Used by the portfolio snapshot job.
    """
    service = PerformanceSessionService(db)
    session = service.get_active_session(user_id)
    
    if not session:
        # Auto-start a session when needed
        session = service.start_session(user_id)
    
    return session
