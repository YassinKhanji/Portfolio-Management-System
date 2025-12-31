"""
Admin Router

Owner-gated endpoints for managing users and roles.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone

import secrets

from ..models.database import SessionLocal, User, Connection, Position
from ..routers.auth import get_current_user, get_password_hash
from ..core.config import get_settings
from ..core.currency import convert_to_cad
from ..core.audit import audit_admin_action, AuditAction
from ..services.snaptrade_integration import get_snaptrade_client, SnapTradeClientError
import logging

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)


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
    # Generate secure random password if not provided
    password = payload.get("password") or secrets.token_urlsafe(16)

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
        created_at=datetime.now(timezone.utc),
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
    user.updated_at = datetime.now(timezone.utc)
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
    user.updated_at = datetime.now(timezone.utc)
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


@router.post("/users/{user_id}/suspend")
def suspend_user(
    user_id: str,
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    """Suspend a user account (server-side enforcement via login guard)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    settings = get_settings()
    if user.email == getattr(settings, "ADMIN_EMAIL", ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot be suspended")

    # Delete SnapTrade users first to stop billing; fail the suspend if upstream deletion fails
    connections = db.query(Connection).filter(Connection.user_id == user.id).all()
    snaptrade_user_ids: set[str] = set()
    for c in connections:
        if c.snaptrade_user_id:
            snaptrade_user_ids.add(c.snaptrade_user_id)

    if snaptrade_user_ids:
        try:
            snaptrade = get_snaptrade_client()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"SnapTrade client init failed during suspend: {exc}",
            )

        failures: list[str] = []
        for sid in snaptrade_user_ids:
            try:
                snaptrade.authentication.delete_snap_trade_user(user_id=sid)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{sid}: {exc}")

        if failures:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to delete SnapTrade user(s): {'; '.join(failures)}",
            )

    # Remove local connections
    for conn in connections:
        db.delete(conn)

    # Mark user inactive
    user.active = False
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "active": user.active,
        "updated_at": user.updated_at.isoformat() + "Z" if user.updated_at else None,
    }


@router.post("/users/{user_id}/unsuspend")
def unsuspend_user(
    user_id: str,
    _: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    """Re-activate a suspended user account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.active = True
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "active": user.active,
        "updated_at": user.updated_at.isoformat() + "Z" if user.updated_at else None,
    }


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    """Delete a user and clean up SnapTrade identities/connections."""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Audit the delete action
    audit_admin_action(
        action=AuditAction.ADMIN_DELETE_CLIENT,
        admin_id=current_user.id,
        admin_email=current_user.email,
        request=request,
        target_user_id=user.id,
        target_user_email=user.email,
        db_session=db,
    )

    # Collect unique SnapTrade user IDs for this user to delete upstream
    connections = db.query(Connection).filter(Connection.user_id == user.id).all()
    snaptrade_user_ids: set[str] = set()
    for c in connections:
        if c.snaptrade_user_id:
            snaptrade_user_ids.add(c.snaptrade_user_id)

    if snaptrade_user_ids:
        try:
            snaptrade = get_snaptrade_client()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"SnapTrade client init failed during user delete: {exc}",
            )

        failures: list[str] = []
        for sid in snaptrade_user_ids:
            try:
                snaptrade.authentication.delete_snap_trade_user(user_id=sid)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{sid}: {exc}")

        if failures:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to delete SnapTrade user(s): {'; '.join(failures)}",
            )

    # Delete local connections then user
    for conn in connections:
        db.delete(conn)
    db.delete(user)
    db.commit()

    return {"message": "User deleted", "id": user_id}


@router.post("/sync-holdings")
def sync_all_holdings_now(
    request: Request,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db)
):
    """
    Manually trigger a holdings sync for all users with SnapTrade connections.
    This fetches live holdings from SnapTrade and updates the Position table.
    """
    from app.jobs.holdings_sync import sync_all_holdings_sync
    
    # Audit the sync action
    audit_admin_action(
        action=AuditAction.ADMIN_SYNC_HOLDINGS,
        admin_id=current_user.id,
        admin_email=current_user.email,
        request=request,
        db_session=db,
    )
    
    try:
        result = sync_all_holdings_sync()
        
        return {
            "message": "Holdings sync complete",
            "synced_users": result.get('users_processed', 0),
            "total_positions": result.get('positions_synced', 0),
            "total_aum": round(result.get('total_aum', 0), 2),
            "errors": result.get('errors', 0)
        }
        
    except Exception as e:
        logger.error(f"Holdings sync failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Holdings sync failed: {str(e)}"
        )
