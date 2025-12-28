"""
Admin Router

Owner-gated endpoints for managing users and roles.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from ..models.database import SessionLocal, User
from ..routers.auth import get_current_user, get_password_hash
from ..core.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_owner(current_user: User = Depends(get_current_user)) -> User:
    settings = get_settings()
    owner_email = getattr(settings, "ADMIN_EMAIL", "")
    if current_user.email != owner_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return current_user


@router.get("/users")
def list_users(
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    users = db.query(User).all()
    settings = get_settings()
    owner_email = getattr(settings, "ADMIN_EMAIL", "")
    result = []
    for u in users:
        is_owner = (u.email == owner_email)
        status_label = "Owner" if is_owner else ("Admin" if (u.role or "").lower() == "admin" else "Client")
        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role or "client",
            "status": status_label,
            "active": bool(u.active),
            "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
            "updated_at": u.updated_at.isoformat() + "Z" if u.updated_at else None,
        })
    return {"users": result}


@router.post("/users")
def create_admin_user(
    payload: dict,
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    email = (payload.get("email") or "").strip().lower()
    full_name = (payload.get("full_name") or payload.get("name") or "").strip()
    password = payload.get("password") or "ChangeMe123!"

    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User with this email already exists")

    hashed = get_password_hash(password)
    user = User(
        email=email,
        password_hash=hashed,
        full_name=full_name or email,
        role="admin",
        active=True,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": "Admin",
        "active": user.active,
        "created_at": user.created_at.isoformat() + "Z" if user.created_at else None,
    }


@router.post("/users/{user_id}/promote")
def promote_user_to_admin(
    user_id: str,
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    settings = get_settings()
    if user.email == getattr(settings, "ADMIN_EMAIL", ""):
        # Already owner; nothing to do
        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role or "admin",
            "status": "Owner",
            "active": user.active,
        }

    user.role = "admin"
    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": "Admin",
        "active": user.active,
        "updated_at": user.updated_at.isoformat() + "Z" if user.updated_at else None,
    }


@router.post("/users/{user_id}/demote")
def demote_user_to_client(
    user_id: str,
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    settings = get_settings()
    if user.email == getattr(settings, "ADMIN_EMAIL", ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot be demoted")

    user.role = "client"
    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": "Client",
        "active": user.active,
        "updated_at": user.updated_at.isoformat() + "Z" if user.updated_at else None,
    }
