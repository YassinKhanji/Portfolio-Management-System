"""
Migration: Add Performance Session Tables

Adds the following tables:
- performance_sessions: Tracks when performance measurement should occur
- benchmark_snapshots: Stores historical benchmark data

Run with: python -m scripts.migrations.add_performance_sessions
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.models.database import engine, SessionLocal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration():
    """Create performance_sessions and benchmark_snapshots tables"""
    
    with engine.connect() as conn:
        # Create performance_sessions table
        logger.info("Creating performance_sessions table...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS performance_sessions (
                id VARCHAR(255) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                baseline_value FLOAT DEFAULT 1.0,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                stopped_at TIMESTAMP WITH TIME ZONE,
                last_snapshot_at TIMESTAMP WITH TIME ZONE,
                benchmark_start_date TIMESTAMP WITH TIME ZONE,
                benchmark_ticker VARCHAR(50) DEFAULT 'SPY',
                metadata_json JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        
        # Create indexes for performance_sessions
        logger.info("Creating indexes for performance_sessions...")
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_perf_session_user_id 
            ON performance_sessions(user_id);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_perf_session_is_active 
            ON performance_sessions(is_active);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_perf_session_started_at 
            ON performance_sessions(started_at);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_perf_session_user_active 
            ON performance_sessions(user_id, is_active);
        """))
        
        # Create benchmark_snapshots table
        logger.info("Creating benchmark_snapshots table...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS benchmark_snapshots (
                id VARCHAR(255) PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                ticker VARCHAR(50) NOT NULL DEFAULT 'SPY',
                value FLOAT NOT NULL,
                return_pct FLOAT DEFAULT 0.0,
                recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                source VARCHAR(100) DEFAULT 'twelve_data'
            );
        """))
        
        # Create indexes for benchmark_snapshots
        logger.info("Creating indexes for benchmark_snapshots...")
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_benchmark_session_id 
            ON benchmark_snapshots(session_id);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_benchmark_recorded_at 
            ON benchmark_snapshots(recorded_at);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_benchmark_session_date 
            ON benchmark_snapshots(session_id, recorded_at);
        """))
        
        conn.commit()
        logger.info("Migration completed successfully!")


def rollback_migration():
    """Drop performance_sessions and benchmark_snapshots tables"""
    
    with engine.connect() as conn:
        logger.info("Rolling back migration...")
        conn.execute(text("DROP TABLE IF EXISTS benchmark_snapshots CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS performance_sessions CASCADE;"))
        conn.commit()
        logger.info("Rollback completed!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Performance Sessions Migration")
    parser.add_argument("--rollback", action="store_true", help="Rollback the migration")
    args = parser.parse_args()
    
    if args.rollback:
        rollback_migration()
    else:
        run_migration()
