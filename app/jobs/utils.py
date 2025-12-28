"""Job utilities for shared runtime checks."""

import logging
from contextlib import suppress
from app.models.database import SessionLocal, SystemStatus

logger = logging.getLogger(__name__)


def is_emergency_stop_active() -> bool:
    """Return True if the system is in emergency-stop mode.

    Falls back to False on any DB error to avoid hard-crashing scheduled jobs,
    while logging the issue for inspection.
    """
    db = SessionLocal()
    try:
        status = db.query(SystemStatus).filter(SystemStatus.id == "system").first()
        return bool(status and status.emergency_stop_active)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to read emergency_stop flag: %s", exc)
        return False
    finally:
        with suppress(Exception):
            db.close()
