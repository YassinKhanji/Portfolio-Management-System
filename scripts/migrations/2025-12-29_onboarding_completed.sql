-- Migration: Add onboarding_completed column to users table
-- Date: 2025-12-29
-- Description: Track whether a client has completed the onboarding process

-- Add onboarding_completed column (defaults to FALSE for new users)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT FALSE;

-- Set existing users who have completed profile setup as onboarded
-- (users who have snaptrade_linked=true or have a custom risk_profile are considered onboarded)
UPDATE users
SET onboarding_completed = TRUE
WHERE snaptrade_linked = TRUE
   OR risk_profile IS NOT NULL AND risk_profile != 'Balanced';

-- Alternatively, mark all existing users as onboarded to avoid forcing them through onboarding again
-- Uncomment the following if you want all existing users to skip onboarding:
-- UPDATE users SET onboarding_completed = TRUE WHERE onboarding_completed IS NULL OR onboarding_completed = FALSE;
