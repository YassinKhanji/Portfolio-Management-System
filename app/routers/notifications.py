"""
Notification preferences endpoints.

Provides read/update for client alert preferences (weekly digest).
"""

import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import SessionLocal, AlertPreference, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NotificationPreferences(BaseModel):
    weekly_digest_enabled: bool


class NotificationPreferencesResponse(NotificationPreferences):
    digest_time: str | None = None


def _get_or_create_pref(db: Session, user_id: str) -> AlertPreference:
    pref = db.query(AlertPreference).filter(AlertPreference.user_id == user_id).first()
    if pref:
        return pref
    pref = AlertPreference(
        id=str(uuid.uuid4()),
        user_id=user_id,
        daily_digest_enabled=True,
    )
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return pref


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_preferences(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = _get_or_create_pref(db, current_user.id)
    return NotificationPreferencesResponse(
        weekly_digest_enabled=bool(pref.daily_digest_enabled),
        digest_time=pref.daily_digest_time,
    )


@router.patch("/preferences", response_model=NotificationPreferencesResponse)
async def update_preferences(
    payload: NotificationPreferences,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = _get_or_create_pref(db, current_user.id)
    pref.daily_digest_enabled = payload.weekly_digest_enabled
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return NotificationPreferencesResponse(
        weekly_digest_enabled=bool(pref.daily_digest_enabled),
        digest_time=pref.daily_digest_time,
    )
