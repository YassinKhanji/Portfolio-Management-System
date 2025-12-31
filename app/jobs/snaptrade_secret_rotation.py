"""SnapTrade User Secret Rotation Job

Rotates SnapTrade user secrets on a schedule (Sunday 01:00 AM EST).

Important:
- SnapTrade's reset endpoint invalidates the old secret.
- We must persist the new secret to the database immediately, otherwise all
  subsequent SnapTrade calls will fail.

This job rotates per unique `snaptrade_user_id` found in the `connections` table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.logging import safe_log_id
from app.jobs.utils import is_emergency_stop_active
from app.models.database import Connection, SessionLocal
from app.services.snaptrade_integration import reset_snaptrade_user_secret

logger = logging.getLogger(__name__)


def rotate_snaptrade_user_secrets() -> dict[str, int]:
    """Rotate all SnapTrade user secrets and persist updates.

    Returns counters for observability/testing.
    """

    if is_emergency_stop_active():
        logger.warning("Skipping SnapTrade secret rotation because emergency_stop is active")
        return {"users_seen": 0, "users_rotated": 0, "users_failed": 0}

    db = SessionLocal()
    users_seen = 0
    users_rotated = 0
    users_failed = 0

    try:
        connections = (
            db.query(Connection)
            .filter(Connection.snaptrade_user_id.isnot(None))
            .all()
        )

        # Build per-user list of candidate secrets (to handle drift/duplicates).
        secrets_by_user: dict[str, set[str]] = {}
        for conn in connections:
            if not conn.snaptrade_user_id or not conn.snaptrade_user_secret:
                continue
            secrets_by_user.setdefault(conn.snaptrade_user_id, set()).add(conn.snaptrade_user_secret)

        for snaptrade_user_id, candidate_secrets in secrets_by_user.items():
            users_seen += 1

            new_secret: str | None = None
            last_error: Exception | None = None

            # Try each stored secret until one succeeds.
            for current_secret in sorted(candidate_secrets):
                try:
                    _, new_secret = reset_snaptrade_user_secret(
                        user_id=snaptrade_user_id,
                        user_secret=current_secret,
                    )
                    break
                except Exception as exc:  # SnapTrade SDK exceptions vary
                    last_error = exc

            if not new_secret:
                users_failed += 1
                logger.error(
                    "SnapTrade secret rotation failed for %s: %s",
                    safe_log_id(snaptrade_user_id, "snaptrade"),
                    last_error,
                )
                db.rollback()
                continue

            # Persist to all connections that share this SnapTrade user id.
            now = datetime.now(timezone.utc)
            affected = (
                db.query(Connection)
                .filter(Connection.snaptrade_user_id == snaptrade_user_id)
                .all()
            )
            for conn in affected:
                conn.snaptrade_user_secret = new_secret
                conn.updated_at = now
                db.add(conn)

            db.commit()
            users_rotated += 1
            logger.info(
                "Rotated SnapTrade secret for %s (updated %d connection(s))",
                safe_log_id(snaptrade_user_id, "snaptrade"),
                len(affected),
            )

        logger.info(
            "[OK] SnapTrade secret rotation complete: users_seen=%d users_rotated=%d users_failed=%d",
            users_seen,
            users_rotated,
            users_failed,
        )
        return {"users_seen": users_seen, "users_rotated": users_rotated, "users_failed": users_failed}

    finally:
        db.close()
