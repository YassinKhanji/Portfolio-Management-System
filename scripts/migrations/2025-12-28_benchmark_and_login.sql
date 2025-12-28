-- Migration: add benchmark status fields and login tracking
-- Run with: psql "$DATABASE_URL" -f 2025-12-28_benchmark_and_login.sql

-- Add benchmark fields to system_status
ALTER TABLE IF EXISTS system_status
    ADD COLUMN IF NOT EXISTS snaptrade_api_available BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS market_data_available BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS benchmark_data_available BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_market_data_refresh TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS last_benchmark_refresh TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS last_health_check TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS last_rebalance TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS current_crypto_regime TEXT NULL,
    ADD COLUMN IF NOT EXISTS current_equities_regime TEXT NULL,
    ADD COLUMN IF NOT EXISTS emergency_stop_active BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS emergency_stop_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS emergency_stop_triggered_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS total_users INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS active_users INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_aum FLOAT DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Logs table compatibility
ALTER TABLE IF EXISTS logs
    ADD COLUMN IF NOT EXISTS admin_action BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS traceback TEXT NULL,
    ADD COLUMN IF NOT EXISTS metadata_json JSON DEFAULT '{}'::json;

-- Add login tracking to users
ALTER TABLE IF EXISTS users
    ADD COLUMN IF NOT EXISTS first_login_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP NULL;

-- Default users.active to FALSE until first login
ALTER TABLE IF EXISTS users
    ALTER COLUMN active SET DEFAULT FALSE;

-- Backfill: mark users who have ever logged in as active (if last_login already populated)
UPDATE users SET active = TRUE WHERE last_login IS NOT NULL;
