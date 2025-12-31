"""
Authentication Router

Handles user authentication with JWT tokens.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import uuid
import jwt
from pydantic import BaseModel, EmailStr, Field, field_validator
import logging
import re
from passlib.context import CryptContext
import html

from ..models.database import SessionLocal, User, Connection, Position
from ..services.snaptrade_integration import (
    register_snaptrade_user,
    build_connect_url,
    list_accounts,
    get_symbol_quote,
    place_equity_order,
    SnapTradeClientError,
    SnapTradeClient,
)
from ..services.email_service import get_email_service
from ..services.market_data import get_live_crypto_prices, get_live_equity_price
from ..core.security import limiter, RATE_LIMITS
from ..core.audit import audit_login, audit_trade, audit_data_access, AuditAction
import os
import httpx
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from ..core.config import get_settings
from ..core.currency import convert_to_cad, get_usd_to_cad_rate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])
settings = get_settings()


def safe_error_message(exc: Exception) -> str:
    """
    Return a safe error message that doesn't leak sensitive information.
    In DEBUG mode, returns full error; in production, returns generic message.
    """
    if settings.DEBUG:
        return str(exc)
    # Log full error for debugging, return safe message to client
    logger.error(f"External service error: {exc}", exc_info=True)
    return "External service temporarily unavailable"


BROKER_CONFIG = {
    "kraken": {"account_type": "crypto"},
    "wealthsimple": {"account_type": "equities"},
}

# Password hashing (support both bcrypt and pbkdf2-sha256)
# Use PBKDF2-SHA256 as the default to avoid bcrypt's 72-byte limit
# Continue to support verifying existing bcrypt hashes
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    default="pbkdf2_sha256",
    deprecated="auto",
    bcrypt__truncate_error=False,
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# JWT settings - SECRET_KEY must be set via environment variable
if not hasattr(settings, 'JWT_SECRET') or not settings.JWT_SECRET:
    raise ValueError("JWT_SECRET must be set via environment variable. Do not use hardcoded keys.")
SECRET_KEY = settings.JWT_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Models
class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict


class UserRegister(BaseModel):
    email: EmailStr = Field(..., description="Valid email address")
    password: str = Field(..., min_length=8, max_length=128, description="Password (8-128 chars)")
    full_name: str = Field(..., min_length=1, max_length=100, description="Full name")
    
    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v):
        """Ensure password meets security requirements."""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one digit')
        return v
    
    @field_validator('full_name')
    @classmethod
    def sanitize_full_name(cls, v):
        """Sanitize name to prevent XSS."""
        return html.escape(v.strip())


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class OrderRequest(BaseModel):
    broker: str = Field(..., pattern=r'^(kraken|wealthsimple)$', description="Supported broker")
    ticker: Optional[str] = Field(None, max_length=20, pattern=r'^[A-Z0-9\.\-]+$')
    universal_symbol_id: Optional[str] = Field(None, max_length=100)
    account_id: Optional[str] = Field(None, max_length=100)
    notional: Optional[float] = Field(None, gt=0, le=1_000_000, description="Trade value in dollars")
    units: Optional[float] = Field(None, gt=0, le=100_000, description="Number of units")
    side: str = Field(..., pattern=r'^(BUY|SELL|buy|sell)$')
    order_type: str = Field("market", pattern=r'^(market|limit|MARKET|LIMIT)$')
    time_in_force: str = Field("DAY", pattern=r'^(DAY|GTC|IOC|FOK)$')
    limit_price: Optional[float] = Field(None, gt=0)


def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_snaptrade_connections(user: User, db: Session) -> dict:
    """Ensure each broker has its own SnapTrade user/secret (no sharing between brokers)."""

    changed = False
    now = datetime.now(timezone.utc)

    # Preload existing connections for this user
    existing = (
        db.query(Connection)
        .filter(Connection.user_id == user.id, Connection.broker.in_(BROKER_CONFIG.keys()))
        .all()
    )
    by_broker = {c.broker: c for c in existing}

    # Detect duplicates across existing connections so we rotate both sides, not just later ones
    id_counts: dict[str, int] = {}
    for c in existing:
        if c.snaptrade_user_id:
            id_counts[c.snaptrade_user_id] = id_counts.get(c.snaptrade_user_id, 0) + 1

    used_snaptrade_ids: set[str] = set()

    for broker, cfg in BROKER_CONFIG.items():
        conn = by_broker.get(broker)
        needs_new_credentials = False

        if not conn:
            # Brand new broker connection: provision a dedicated SnapTrade user for this broker
            user_id_hint = f"{user.id}-{broker}"
            uid, secret = register_snaptrade_user(user_id_hint)
            conn = Connection(
                id=str(uuid.uuid4()),
                user_id=user.id,
                snaptrade_user_id=uid,
                snaptrade_user_secret=secret,
                account_type=cfg["account_type"],
                broker=broker,
                is_connected=False,
                connection_status="pending",
                created_at=now,
                updated_at=now,
            )
            db.add(conn)
            changed = True
        else:
            # Existing connection: decide if it needs fresh credentials
            duplicate_seen = conn.snaptrade_user_id in used_snaptrade_ids
            duplicate_anywhere = id_counts.get(conn.snaptrade_user_id or "", 0) > 1
            if not conn.snaptrade_user_id or not conn.snaptrade_user_secret or duplicate_seen or duplicate_anywhere:
                needs_new_credentials = True

            if needs_new_credentials:
                # Add a short suffix to reduce collision retries when rotating duplicates
                user_id_hint = f"{user.id}-{broker}-{uuid.uuid4().hex[:6]}"
                uid, secret = register_snaptrade_user(user_id_hint)
                conn.snaptrade_user_id = uid
                conn.snaptrade_user_secret = secret
                conn.is_connected = False
                conn.connection_status = "pending"
                conn.account_id = None  # clear stale account binding when rotating credentials
                conn.updated_at = now
                db.add(conn)
                changed = True

        # Track used SnapTrade IDs to detect duplicates within this user
        if conn.snaptrade_user_id:
            used_snaptrade_ids.add(conn.snaptrade_user_id)

    if changed:
        user.snaptrade_linked = False
        user.updated_at = now
        db.add(user)
        db.commit()
        db.refresh(user)

    connections = (
        db.query(Connection)
        .filter(Connection.user_id == user.id, Connection.broker.in_(BROKER_CONFIG.keys()))
        .all()
    )
    return {c.broker: c for c in connections}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def authenticate_user(db: Session, email: str, password: str):
    """Authenticate user by email and password"""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return False
    if not verify_password(password, user.password_hash):
        return False
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Get current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user


def get_current_user_optional(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Optional[User]:
    """Return user if a Bearer token is provided; otherwise None.

    SnapTrade callback redirects cannot send Authorization headers, so we allow
    anonymous access and later validate using userId/userSecret against stored
    connections.
    """

    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            return None
    except jwt.PyJWTError:
        return None

    return db.query(User).filter(User.email == email).first()


def _get_connection_or_400(db: Session, user: User, broker: str) -> Connection:
    broker = broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")
    connections = ensure_snaptrade_connections(user, db)
    connection = connections.get(broker)
    if not connection:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection not initialized")
    return connection


@router.get("/auth/snaptrade/connect/{broker}")
async def get_snaptrade_connect_url(
    broker: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    broker = broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    client_id = getattr(settings, "SNAPTRADE_CLIENT_ID", "")
    client_secret = getattr(settings, "SNAPTRADE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SnapTrade credentials missing")

    connections = ensure_snaptrade_connections(current_user, db)
    connection = connections.get(broker)
    if not connection:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initialize connection")

    # Build custom redirect URL with our internal connection_id and broker for callback resolution
    base_redirect = settings.SNAPTRADE_REDIRECT_URI or "http://localhost:8000/api/auth/snaptrade/callback"
    # Add internal_connection_id and broker to the redirect URL so callback can resolve the connection
    redirect_params = urlencode({"internal_connection_id": connection.id, "broker": broker})
    custom_redirect = f"{base_redirect}?{redirect_params}"

    # Build connect URL; if SnapTrade rejects credentials (1083), rotate creds once and retry
    try:
        connect_url = build_connect_url(
            user_id=connection.snaptrade_user_id,
            user_secret=connection.snaptrade_user_secret,
            broker=None,  # avoid preselecting unsupported institutions
            connection_type="trade",
            custom_redirect=custom_redirect,
            immediate_redirect=True,
        )
    except SnapTradeClientError as exc:
        msg = str(exc)
        if "1083" in msg or "Invalid userID" in msg or "userSecret" in msg:
            # Rotate credentials and retry once
            uid, secret = register_snaptrade_user(f"{current_user.id}-{broker}-{uuid.uuid4().hex[:6]}")
            connection.snaptrade_user_id = uid
            connection.snaptrade_user_secret = secret
            connection.is_connected = False
            connection.connection_status = "pending"
            connection.account_id = None
            connection.updated_at = datetime.now(timezone.utc)
            db.add(connection)
            db.commit()
            db.refresh(connection)

            # Rebuild custom redirect with updated connection id
            redirect_params = urlencode({"internal_connection_id": connection.id, "broker": broker})
            custom_redirect = f"{base_redirect}?{redirect_params}"

            try:
                connect_url = build_connect_url(
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret,
                    broker=None,
                    connection_type="trade",
                    custom_redirect=custom_redirect,
                    immediate_redirect=True,
                )
            except SnapTradeClientError as exc2:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc2))
        else:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc))

    return {
        "connect_url": connect_url,
        "broker": broker,
        "connection_type": "trade",
        "connection_id": connection.id,
        "snaptrade_user_id": connection.snaptrade_user_id,
    }


@router.get("/auth/snaptrade/callback")
async def snaptrade_callback(
    broker: Optional[str] = Query(default=None),
    code: str = "",  # placeholder for future validation
    userId: str = "",
    userSecret: str = "",
    accountId: str = "",
    connection_id: str = "",
    connectionId: str = "",  # SnapTrade may return camelCase
    internal_connection_id: str = "",  # Our internal connection ID passed via custom redirect
    status_param: str = "",  # absorb status=SUCCESS from SnapTrade
    format: str = "",  # optional override for HTML response
    request: Request = None,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Handle SnapTrade redirect and mark the broker connection as linked.

    The SnapTrade redirect does not include an Authorization header. We accept
    either a Bearer token (preferred) or the userId/userSecret pair provided by
    SnapTrade and validate it against the stored Connection before linking.
    
    Resolution order:
    1. internal_connection_id (our UUID passed via custom redirect - most reliable)
    2. SnapTrade credentials (userId + userSecret)
    3. Authenticated user + broker hint
    """
    broker = broker.lower() if broker else None
    if broker and broker not in BROKER_CONFIG:
        logger.warning(
            "SnapTrade callback with unsupported broker",
            extra={"broker": broker, "connection_id": connection_id or connectionId, "userId": userId},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    # SnapTrade's connection_id is NOT our internal ID - store it for reference but don't use for lookup
    snaptrade_connection_id = connection_id or connectionId

    connection: Optional[Connection] = None

    # 1. Resolve by our internal connection ID (most reliable - passed via custom redirect)
    if internal_connection_id:
        connection = db.query(Connection).filter(Connection.id == internal_connection_id).first()
        if connection:
            current_user = db.query(User).filter(User.id == connection.user_id).first()
            if not broker:
                broker = connection.broker
            logger.info(
                "SnapTrade callback resolved via internal_connection_id",
                extra={"internal_connection_id": internal_connection_id, "broker": broker},
            )

    # 2. Fall back to SnapTrade credentials (userId + userSecret)
    # Note: snaptrade_user_secret is now encrypted, so we find by userId first
    # then verify the decrypted secret matches
    if not connection and userId and userSecret:
        query = db.query(Connection).filter(
            Connection.snaptrade_user_id == userId,
        )
        if broker:
            query = query.filter(Connection.broker == broker)
        # Find all matching connections and verify secret
        for conn in query.all():
            if conn.snaptrade_user_secret == userSecret:
                connection = conn
                break
        if connection:
            if not broker:
                broker = connection.broker
            current_user = db.query(User).filter(User.id == connection.user_id).first()
            logger.info(
                "SnapTrade callback resolved via userId/userSecret",
                extra={"userId": userId, "broker": broker},
            )

    # 3. Use authenticated user plus broker hint
    if not connection and current_user and broker:
        connections = ensure_snaptrade_connections(current_user, db)
        connection = connections.get(broker)
        if connection:
            logger.info(
                "SnapTrade callback resolved via authenticated user + broker",
                extra={"user_id": current_user.id, "broker": broker},
            )

    if not connection or not current_user:
        logger.error(
            "SnapTrade callback could not resolve connection",
            extra={
                "broker_param": broker,
                "internal_connection_id_param": internal_connection_id,
                "snaptrade_connection_id_param": snaptrade_connection_id,
                "userId_param": userId,
                "has_user": bool(current_user),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "reason": "unable_to_resolve_connection",
                "broker": broker,
                "connection_id": snaptrade_connection_id,
                "internal_connection_id": internal_connection_id,
            },
        )

    # Ensure broker matches the connection row (guard against mismatched redirect params)
    if broker and broker != connection.broker:
        logger.error(
            "SnapTrade callback broker mismatch",
            extra={"broker_param": broker, "connection_broker": connection.broker, "connection_id": connection.id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "broker_mismatch", "expected": connection.broker, "provided": broker},
        )
    broker = connection.broker  # normalize downstream

    if userId and userId != connection.snaptrade_user_id:
        logger.error(
            "SnapTrade callback userId mismatch",
            extra={"connection_id": connection.id, "expected": connection.snaptrade_user_id, "provided": userId},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"reason": "user_mismatch"})
    if userSecret and userSecret != connection.snaptrade_user_secret:
        logger.error(
            "SnapTrade callback secret mismatch",
            extra={"connection_id": connection.id, "userId": connection.snaptrade_user_id},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"reason": "secret_mismatch"})

    now = datetime.now(timezone.utc)
    connection.is_connected = True
    connection.connection_status = "connected"
    connection.account_id = accountId or connection.account_id
    connection.updated_at = now
    db.add(connection)

    # Mark user as linked if all broker connections are connected
    all_conns = (
        db.query(Connection)
        .filter(Connection.user_id == current_user.id, Connection.broker.in_(BROKER_CONFIG.keys()))
        .all()
    )
    if all_conns and all(c.is_connected for c in all_conns):
        current_user.snaptrade_linked = True
        current_user.updated_at = now
        db.add(current_user)

    db.commit()
    db.refresh(current_user)

    logger.info(
        "SnapTrade callback linked connection",
        extra={
            "broker": broker,
            "connection_id": connection.id,
            "user_id": current_user.id,
            "snaptrade_user_id": connection.snaptrade_user_id,
        },
    )

    if format and format.lower() == "json":
        return {
            "status": "connected",
            "broker": broker,
            "connection_id": connection.id,
            "account_id": connection.account_id,
            "snaptrade_user_id": connection.snaptrade_user_id,
        }

    # Sanitize broker name to prevent XSS - use only known broker names or escape
    safe_broker_name = html.escape(broker.title()) if broker else "Your"
    
    # Always return a visual completion page so the portal shows "Done"
    html_content = f"""
    <html>
        <body style='margin:0; padding:0; font-family: Helvetica, Arial, sans-serif; background:#f8f9fb;'>
            <div style='max-width: 520px; margin: 32px auto; background:#fff; border-radius:24px; padding:32px; box-shadow:0 8px 28px rgba(0,0,0,0.12); text-align:center;'>
                <div style='display:flex; justify-content:center; gap:16px; align-items:center; margin-bottom:24px;'>
                    <div style='width:56px; height:56px; border-radius:12px; border:1px solid #e6e8ee; display:flex; align-items:center; justify-content:center; font-size:24px;'>↔</div>
                    <div style='width:56px; height:56px; border-radius:12px; border:1px solid #e6e8ee; display:flex; align-items:center; justify-content:center; font-size:26px; font-weight:700;'>W</div>
                </div>
                <div style='width:140px; height:140px; margin:0 auto 24px; border-radius:50%; background:#e8f4f2; display:flex; align-items:center; justify-content:center;'>
                    <div style='width:88px; height:88px; border-radius:50%; background:#d1ece6; display:flex; align-items:center; justify-content:center;'>
                        <div style='width:52px; height:52px; border-radius:14px; background:#0f766e; display:flex; align-items:center; justify-content:center; color:#fff; font-size:26px;'>✓</div>
                    </div>
                </div>
                <h2 style='margin:0 0 12px; font-size:28px; color:#111827;'>Connection Complete</h2>
                <p style='margin:0 0 8px; font-size:16px; color:#374151;'>Your {safe_broker_name} account has been successfully connected.</p>
                <p style='margin:0 0 24px; font-size:14px; color:#6b7280;'>You can close this window.</p>
                <a href='javascript:window.close();' style='display:block; width:100%; background:#111; color:#fff; text-decoration:none; padding:14px 0; border-radius:18px; font-size:17px; font-weight:600;'>Done</a>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content, status_code=200)


@router.get("/auth/snaptrade/accounts/{broker}")
async def snaptrade_accounts(
    broker: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List accounts for a given broker connection and persist the first account ID if missing."""

    broker = broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    connections = ensure_snaptrade_connections(current_user, db)
    connection = connections.get(broker)
    if not connection:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection not initialized")

    try:
        accounts = list_accounts(connection.snaptrade_user_id, connection.snaptrade_user_secret)
    except SnapTradeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc

    # Persist first account id if not already stored
    if accounts and not connection.account_id:
        first_id = accounts[0].get("id") or accounts[0].get("account_id")
        if first_id:
            connection.account_id = first_id
            connection.updated_at = datetime.now(timezone.utc)
            db.add(connection)
            db.commit()

    return {"accounts": accounts, "saved_account_id": connection.account_id}


@router.get("/auth/snaptrade/status")
async def snaptrade_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return connection status for all supported brokers for the current user."""

    connections = ensure_snaptrade_connections(current_user, db)
    items = []
    for broker, conn in connections.items():
        items.append(
            {
                "broker": broker,
                "connection_id": conn.id,
                "is_connected": bool(conn.is_connected),
                "connection_status": conn.connection_status,
                "account_id": conn.account_id,
                "snaptrade_user_id": conn.snaptrade_user_id,
                "updated_at": conn.updated_at.isoformat() + "Z" if conn.updated_at else None,
            }
        )

    return {
        "snaptrade_linked": bool(current_user.snaptrade_linked),
        "brokers": items,
    }


@router.post("/auth/snaptrade/sync")
async def snaptrade_sync_user_holdings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Sync the current user's holdings from SnapTrade.
    Updates positions, calculates cost basis from order history, and refreshes return percentages.
    Called automatically on login and can be triggered manually.
    """
    from app.jobs.holdings_sync import sync_user_holdings_sync
    
    if not current_user.snaptrade_linked:
        return {
            "message": "No SnapTrade connection",
            "positions_synced": 0,
            "total_value": 0,
        }
    
    try:
        result = sync_user_holdings_sync(current_user.id, db)
        return {
            "message": "Holdings synced successfully",
            "positions_synced": result.get("positions_synced", 0),
            "total_value": round(result.get("total_value", 0), 2),
        }
    except Exception as e:
        logger.error(f"Holdings sync failed for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Holdings sync failed: {str(e)}"
        )


@router.get("/auth/snaptrade/holdings")
async def snaptrade_holdings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return aggregated holdings and account balances from connected SnapTrade brokers."""

    connections = ensure_snaptrade_connections(current_user, db)

    accounts_out = []
    positions_out = []
    total_value = 0.0
    errors: list[str] = []
    
    # Collect order data for all connections to get cost basis and order times
    all_orders_by_symbol: dict[str, dict] = {}
    # Also collect cost basis from activities (for brokers that don't return it in holdings)
    all_cost_basis: dict[str, float] = {}

    for broker, conn in connections.items():
        if not conn.is_connected or not conn.snaptrade_user_id or not conn.snaptrade_user_secret:
            continue
        try:
            client = SnapTradeClient(conn.snaptrade_user_id, conn.snaptrade_user_secret)
            accounts = client.get_accounts()
            logger.info(f"Retrieved {len(accounts)} accounts for {broker}")
            
            # Fetch order history to get accurate cost basis and order times
            try:
                orders_by_symbol = client.get_last_orders_by_symbol(days=365)  # Look back 1 year
                for symbol, order_data in orders_by_symbol.items():
                    symbol_upper = symbol.upper()
                    if symbol_upper not in all_orders_by_symbol:
                        all_orders_by_symbol[symbol_upper] = order_data
                    else:
                        # Keep the most recent order
                        existing_time = all_orders_by_symbol[symbol_upper].get('time_placed')
                        new_time = order_data.get('time_placed')
                        if new_time and (not existing_time or new_time > existing_time):
                            all_orders_by_symbol[symbol_upper] = order_data
                logger.info(f"Fetched order history for {len(orders_by_symbol)} symbols from {broker}")
            except Exception as order_exc:
                logger.warning(f"Could not fetch order history from {broker}: {order_exc}")
            
            # Also fetch activities to calculate cost basis (fallback method)
            try:
                activities = client.get_all_account_activities()
                cost_basis_from_activities = client.calculate_cost_basis(activities)
                for symbol, cost in cost_basis_from_activities.items():
                    symbol_upper = symbol.upper()
                    if symbol_upper not in all_cost_basis:
                        all_cost_basis[symbol_upper] = cost
                logger.info(f"Calculated cost basis from activities for {len(cost_basis_from_activities)} symbols from {broker}")
            except Exception as activity_exc:
                logger.warning(f"Could not fetch activities from {broker}: {activity_exc}")
                
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{broker}: {exc}")
            logger.error(f"Failed to get accounts for {broker}: {exc}")
            continue

        for acct in accounts:
            logger.info(f"Processing account {acct.name} ({acct.id}) - balance={acct.balance}, buying_power={acct.buying_power}")
            
            # Default cash from account info (fallback)
            acct_balance = float(acct.balance or 0)
            acct_buying_power = float(acct.buying_power or 0)
            fallback_cash = max(acct_balance, acct_buying_power)
            logger.info(f"Account fallback cash calculated as max({acct_balance}, {acct_buying_power}) = {fallback_cash}")

            try:
                holdings_result = client.get_holdings(acct.id)
                holdings = holdings_result.holdings
                # Use the actual cash from balances field, not account.balance (which may include securities)
                acct_cash = holdings_result.total_cash if holdings_result.total_cash > 0 else fallback_cash
                logger.info(f"Retrieved {len(holdings)} holdings for account {acct.id}, actual cash from balances: {holdings_result.total_cash}, using: {acct_cash}")
                
                # Fetch live crypto prices for Kraken holdings
                if broker == "kraken" and holdings:
                    try:
                        # Get symbols for all non-cash holdings
                        crypto_symbols = []
                        for h in holdings:
                            symbol_val = h.symbol
                            # Handle nested symbol structure
                            max_depth = 3
                            depth = 0
                            while isinstance(symbol_val, dict) and depth < max_depth:
                                inner = symbol_val.get("symbol") or symbol_val.get("raw_symbol")
                                if inner is None:
                                    symbol_val = str(symbol_val.get("id", symbol_val))
                                    break
                                symbol_val = inner
                                depth += 1
                            if not isinstance(symbol_val, str):
                                symbol_val = str(symbol_val) if symbol_val else ""
                            
                            # Skip stablecoins and fiat
                            symbol_upper = symbol_val.upper().replace(".CX", "")
                            if symbol_upper not in {"USDC", "USDT", "DAI", "USD", "CAD", "EUR"}:
                                crypto_symbols.append(symbol_val)
                        
                        if crypto_symbols:
                            live_prices_usd = get_live_crypto_prices(crypto_symbols)
                            logger.info(f"Fetched live prices for {len(live_prices_usd)} crypto assets: {live_prices_usd}")
                            
                            # Update holdings with live prices
                            for h in holdings:
                                symbol_val = h.symbol
                                # Handle nested structure again
                                max_depth = 3
                                depth = 0
                                while isinstance(symbol_val, dict) and depth < max_depth:
                                    inner = symbol_val.get("symbol") or symbol_val.get("raw_symbol")
                                    if inner is None:
                                        symbol_val = str(symbol_val.get("id", symbol_val))
                                        break
                                    symbol_val = inner
                                    depth += 1
                                if not isinstance(symbol_val, str):
                                    symbol_val = str(symbol_val) if symbol_val else ""
                                
                                symbol_upper = symbol_val.upper()
                                if symbol_upper in live_prices_usd:
                                    old_price = h.price
                                    h.price = live_prices_usd[symbol_upper]
                                    h.market_value = h.quantity * h.price
                                    logger.info(f"Updated {symbol_upper} price from {old_price} to {h.price} USD (live)")
                    except Exception as live_price_exc:
                        logger.warning(f"Failed to fetch live crypto prices: {live_price_exc}")
                        # Continue with SnapTrade prices as fallback
                        
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{broker}/{acct.id}: {exc}")
                logger.error(f"Failed to get holdings for {broker}/{acct.id}: {exc}")
                holdings = []
                acct_cash = fallback_cash

            # Stablecoins and fiat currencies that should be classified as "cash"
            STABLECOINS = {"USDC", "USDT", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX", "LUSD", "USDD", "PYUSD"}
            FIAT_CURRENCIES = {"USD", "CAD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "HKD", "SGD"}
            CASH_SYMBOLS = STABLECOINS | FIAT_CURRENCIES

            for h in holdings:
                logger.info(f"Processing holding: symbol={h.symbol}, qty={h.quantity}, price={h.price}, value={h.market_value}")
                
                # Ensure symbol is always a string (handle SnapTrade deeply nested objects)
                # SnapTrade can return: symbol as string, or symbol as dict with 'symbol' key,
                # or symbol as dict with nested dict {'symbol': {'symbol': 'ATOM', ...}}
                symbol_val = h.symbol
                
                # Handle multiple levels of nesting
                max_depth = 3  # Safety limit
                depth = 0
                while isinstance(symbol_val, dict) and depth < max_depth:
                    # Try to extract the symbol string from various possible keys
                    inner = symbol_val.get("symbol") or symbol_val.get("raw_symbol")
                    if inner is None:
                        # No more nesting, convert dict to string
                        symbol_val = str(symbol_val.get("id", symbol_val))
                        break
                    symbol_val = inner
                    depth += 1
                
                if not isinstance(symbol_val, str):
                    symbol_val = str(symbol_val) if symbol_val else ""
                
                logger.info(f"Extracted symbol string: '{symbol_val}' from original: {h.symbol}")
                
                # Handle name similarly
                name_val = h.name
                max_depth = 3
                depth = 0
                while isinstance(name_val, dict) and depth < max_depth:
                    inner = name_val.get("description") or name_val.get("name") or name_val.get("symbol")
                    if inner is None:
                        name_val = symbol_val
                        break
                    name_val = inner
                    depth += 1
                
                if not isinstance(name_val, str):
                    name_val = str(name_val) if name_val else symbol_val
                
                # Determine asset class: stablecoins/fiat -> cash, otherwise based on broker
                symbol_upper = symbol_val.upper().replace(".CX", "")  # Handle variants like USD.CX
                if symbol_upper in CASH_SYMBOLS:
                    asset_class = "cash"
                elif broker == "kraken":
                    asset_class = "crypto"
                else:
                    asset_class = "equity"
                
                # Get the reported currency from holding - SnapTrade reports in USD typically
                original_currency = h.currency or "USD"
                
                # Log raw values from SnapTrade for debugging
                logger.info(f"RAW from SnapTrade - {symbol_val}: price={h.price}, avg_price={h.average_purchase_price}, currency={original_currency}, broker={broker}")
                
                # Convert price and market_value to CAD 
                price_cad = convert_to_cad(h.price, original_currency)
                market_value_cad = convert_to_cad(h.market_value, original_currency)
                
                # Get average purchase price (cost basis) from holding and convert to CAD
                avg_purchase_price = getattr(h, 'average_purchase_price', 0) or 0
                if avg_purchase_price and avg_purchase_price > 0:
                    avg_purchase_price = convert_to_cad(avg_purchase_price, original_currency)
                    logger.info(f"Converted avg_purchase_price to CAD: {h.average_purchase_price} {original_currency} -> {avg_purchase_price} CAD")
                
                logger.info(f"PROCESSED - {symbol_val}: price_cad={price_cad}, avg_price_cad={avg_purchase_price}, original_currency={original_currency}")
                
                positions_out.append(
                    {
                        "broker": broker,
                        "account_id": acct.id,
                        "account_name": acct.name,
                        "symbol": symbol_val,
                        "name": name_val,
                        "quantity": h.quantity,
                        "price": price_cad,
                        "market_value": market_value_cad,
                        "currency": "CAD",  # All values now in CAD
                        "original_currency": original_currency,  # Keep original for reference
                        "asset_class": asset_class,
                        "average_purchase_price": avg_purchase_price,  # Cost basis in CAD
                    }
                )
            
            # Sum holdings value from the positions we just created (already in CAD)
            # Filter to only non-cash positions from this account
            holdings_value = sum(
                p["market_value"] for p in positions_out 
                if p["account_id"] == acct.id and p["asset_class"] != "cash"
            )
            logger.info(f"Total holdings value for {acct.id} (in CAD): {holdings_value}")
            
            # Convert cash to CAD as well
            cash_currency = acct.currency or "USD"
            acct_cash_cad = convert_to_cad(acct_cash, cash_currency)
            logger.info(f"Cash for {acct.id}: {acct_cash} {cash_currency} -> {acct_cash_cad} CAD")
            
            # Add cash balance as a position so it shows up in the Cash category
            if acct_cash_cad > 0:
                # Include broker name in the cash display so users know where it's from
                broker_display = broker.title()  # "kraken" -> "Kraken", "wealthsimple" -> "Wealthsimple"
                positions_out.append(
                    {
                        "broker": broker,
                        "account_id": acct.id,
                        "account_name": acct.name,
                        "symbol": f"CAD",
                        "name": f"Cash (CAD) - {broker_display}",
                        "quantity": acct_cash_cad,
                        "price": 1.0,
                        "market_value": acct_cash_cad,
                        "currency": "CAD",
                        "asset_class": "cash",
                    }
                )
            
            acct_total = holdings_value + acct_cash_cad
            total_value += acct_total
            logger.info(f"Account {acct.id} total: holdings({holdings_value}) + cash({acct_cash_cad}) = {acct_total}")

            accounts_out.append(
                {
                    "broker": broker,
                    "account_id": acct.id,
                    "name": acct.name,
                    "type": acct.type,
                    "currency": acct.currency,
                    "balance": acct.balance,
                    "buying_power": acct.buying_power,
                    "market_value": holdings_value,
                    "total_value": acct_total,
                }
            )
    
    logger.info(f"Total portfolio value: {total_value}")
    logger.info(f"Total positions returned: {len(positions_out)}")

    # Sync positions to the database for AUM tracking and snapshots
    # First, get existing positions to preserve cost_basis and order tracking
    existing_positions = {
        p.symbol: p for p in db.query(Position).filter(Position.user_id == current_user.id).all()
    }
    
    try:
        # Delete existing positions for this user and re-insert fresh data
        db.query(Position).filter(Position.user_id == current_user.id).delete()
        
        for pos in positions_out:
            symbol = pos["symbol"]
            symbol_upper = symbol.upper()
            existing = existing_positions.get(symbol)
            
            # Get order data from SnapTrade orders API (includes weighted avg buy price)
            order_data = all_orders_by_symbol.get(symbol_upper, {})
            
            # Get cost_basis from multiple sources (priority order):
            # 1. Average purchase price from SnapTrade holdings API (provided by broker)
            # 2. Weighted average BUY price from order history
            # 3. Cost basis calculated from activities
            # 4. Existing position in database (preserved from previous syncs)
            cost_basis = None
            
            # Priority 1: Average purchase price from holdings API (most reliable - directly from broker)
            if pos.get("average_purchase_price") and pos["average_purchase_price"] > 0:
                cost_basis = pos["average_purchase_price"]
                logger.info(f"Using average_purchase_price {cost_basis} as cost_basis for {symbol} (from holdings API, already in CAD)")
            
            # Priority 2: Weighted average buy price from order history
            if not cost_basis:
                avg_buy_price = order_data.get('avg_buy_price', 0)
                if avg_buy_price and avg_buy_price > 0:
                    # Order history prices from SnapTrade are in USD - convert to CAD
                    original_currency = pos.get("original_currency", "USD")
                    avg_buy_price = convert_to_cad(avg_buy_price, original_currency)
                    cost_basis = avg_buy_price
                    logger.info(f"Using avg_buy_price {cost_basis} CAD as cost_basis for {symbol} (from order history, converted from {original_currency})")
            
            # Priority 3: Cost basis from activities
            if not cost_basis:
                activity_cost = all_cost_basis.get(symbol_upper, 0)
                if activity_cost and activity_cost > 0:
                    # Activities prices from SnapTrade are in USD - convert to CAD
                    original_currency = pos.get("original_currency", "USD")
                    activity_cost = convert_to_cad(activity_cost, original_currency)
                    cost_basis = activity_cost
                    logger.info(f"Using activity-based cost_basis {cost_basis} CAD for {symbol} (converted from {original_currency})")
            
            # Priority 4: Existing database value
            if not cost_basis and existing and existing.cost_basis and existing.cost_basis > 0:
                cost_basis = existing.cost_basis
                logger.info(f"Using existing DB cost_basis {cost_basis} for {symbol}")
            
            # Get last_order_time from order data or existing position
            last_order_time = None
            if order_data.get('time_placed'):
                last_order_time = order_data['time_placed']
                if isinstance(last_order_time, str):
                    try:
                        last_order_time = datetime.fromisoformat(last_order_time.replace('Z', '+00:00'))
                    except ValueError:
                        last_order_time = None
            elif existing and existing.last_order_time:
                last_order_time = existing.last_order_time
            
            # Get last_order_side from order data or existing position
            last_order_side = order_data.get('action') or (existing.last_order_side if existing else None) or "HOLD"
            
            # Calculate change from cost basis (this is the P&L percentage)
            change_24h = 0.0
            if cost_basis and cost_basis > 0 and pos["price"]:
                change_24h = ((pos["price"] - cost_basis) / cost_basis) * 100
                logger.info(f"Calculated return for {symbol}: current_price={pos['price']:.6f}, cost_basis={cost_basis:.6f}, return={change_24h:.2f}%")
            else:
                logger.warning(f"Cannot calculate return for {symbol}: cost_basis={cost_basis}, price={pos.get('price')}")
            
            # Add these fields to the position output for the API response
            pos["cost_basis"] = cost_basis
            pos["change_24h"] = change_24h
            pos["last_order_time"] = last_order_time.isoformat() if last_order_time else None
            pos["last_order_side"] = last_order_side or "HOLD"
            
            position = Position(
                id=str(uuid.uuid4()),
                user_id=current_user.id,
                symbol=symbol,
                quantity=float(pos["quantity"]),
                price=float(pos["price"]),
                market_value=float(pos["market_value"]),
                cost_basis=cost_basis,
                last_order_time=last_order_time,
                last_order_side=last_order_side,
                allocation_percentage=0.0,  # Will be calculated on demand
                target_percentage=0.0,
                metadata_json={
                    "broker": pos["broker"],
                    "account_id": pos["account_id"],
                    "account_name": pos["account_name"],
                    "name": pos["name"],
                    "currency": pos.get("currency", "CAD"),
                    "original_currency": pos.get("original_currency", "CAD"),
                    "asset_class": pos["asset_class"],
                },
            )
            db.add(position)
        
        db.commit()
        logger.info(f"Synced {len(positions_out)} positions to database for user {current_user.id}")
    except Exception as sync_error:
        db.rollback()
        logger.error(f"Failed to sync positions to database: {sync_error}")
        # Don't fail the request, just log the error - holdings are still returned

    return {
        "total_value": total_value,
        "accounts": accounts_out,
        "positions": positions_out,
        "errors": errors,
    }


@router.get("/auth/snaptrade/symbol/{ticker}")
async def snaptrade_symbol_lookup(
    ticker: str,
    broker: str,
    account_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resolve universal symbol id and quote for a ticker on a broker/account."""

    broker = broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    connections = ensure_snaptrade_connections(current_user, db)
    connection = connections.get(broker)
    if not connection:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection not initialized")

    account = account_id or connection.account_id
    if not account:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account not linked yet")

    try:
        quote = get_symbol_quote(ticker, account, connection.snaptrade_user_id, connection.snaptrade_user_secret)
    except SnapTradeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc

    universal_symbol_id = None
    if isinstance(quote, dict):
        universal_symbol_id = (
            quote.get("universal_symbol", {}).get("id")
            or quote.get("universal_symbol_id")
            or quote.get("symbol_id")
        )

    return {
        "quote": quote,
        "universal_symbol_id": universal_symbol_id,
        "account_id": account,
        "broker": broker,
    }


def _extract_price(quote: dict) -> float:
    """Best-effort extraction of a price field from SnapTrade quote."""

    price_fields = ["price", "last", "last_trade_price", "ask", "bid"]
    for field in price_fields:
        value = quote.get(field)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):  # noqa: PERF203
                continue
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price unavailable for ticker")


@router.post("/auth/snaptrade/orders")
async def snaptrade_place_order(
    request: Request,
    order: OrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Place an order with optional notional sizing and IOC fallback for time-in-force."""

    broker = order.broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    connections = ensure_snaptrade_connections(current_user, db)
    connection = connections.get(broker)
    if not connection:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection not initialized")

    account_id = order.account_id or connection.account_id
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account not linked yet")

    if not order.universal_symbol_id and not order.ticker:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ticker or universal_symbol_id required")

    quote = None
    universal_symbol_id = order.universal_symbol_id
    if not universal_symbol_id:
        try:
            quote = get_symbol_quote(order.ticker, account_id, connection.snaptrade_user_id, connection.snaptrade_user_secret)
            universal_symbol_id = (
                quote.get("universal_symbol", {}).get("id")
                or quote.get("universal_symbol_id")
                or quote.get("symbol_id")
            )
        except SnapTradeClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc

    if not universal_symbol_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Universal symbol id not resolved")

    units = order.units
    if not units:
        try:
            quote = quote or get_symbol_quote(
                order.ticker, account_id, connection.snaptrade_user_id, connection.snaptrade_user_secret
            )
        except SnapTradeClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc
        price = _extract_price(quote)
        if not order.notional:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Notional or units required")
        if price <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid price returned for ticker")
        units = order.notional / price

    time_in_force = order.time_in_force.upper()
    order_type = order.order_type.lower()

    try:
        placed = place_equity_order(
            account_id=account_id,
            user_id=connection.snaptrade_user_id,
            user_secret=connection.snaptrade_user_secret,
            universal_symbol_id=universal_symbol_id,
            action=order.side,
            order_type=order_type,
            time_in_force=time_in_force,
            units=units,
            limit_price=order.limit_price,
        )
    except SnapTradeClientError as exc:
        # Retry once with IOC fallback if time in force is not already IOC
        if time_in_force != "IOC":
            try:
                placed = place_equity_order(
                    account_id=account_id,
                    user_id=connection.snaptrade_user_id,
                    user_secret=connection.snaptrade_user_secret,
                    universal_symbol_id=universal_symbol_id,
                    action=order.side,
                    order_type=order_type,
                    time_in_force="IOC",
                    units=units,
                    limit_price=order.limit_price,
                )
            except SnapTradeClientError as exc:  # noqa: PERF203
                # Audit failed trade
                audit_trade(
                    action=AuditAction.TRADE_FAILED,
                    user_id=current_user.id,
                    user_email=current_user.email,
                    request=request,
                    symbol=order.ticker,
                    side=order.side,
                    quantity=units,
                    success=False,
                    error=str(exc),
                    db_session=db,
                )
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc
        else:
            # Audit failed trade
            audit_trade(
                action=AuditAction.TRADE_FAILED,
                user_id=current_user.id,
                user_email=current_user.email,
                request=request,
                symbol=order.ticker,
                side=order.side,
                quantity=units,
                success=False,
                error=str(exc),
                db_session=db,
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # Audit failed trade
        audit_trade(
            action=AuditAction.TRADE_FAILED,
            user_id=current_user.id,
            user_email=current_user.email,
            request=request,
            symbol=order.ticker,
            side=order.side,
            quantity=units,
            success=False,
            error=str(exc),
            db_session=db,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_error_message(exc)) from exc

    # Audit successful trade
    audit_trade(
        action=AuditAction.ORDER_PLACED,
        user_id=current_user.id,
        user_email=current_user.email,
        request=request,
        symbol=order.ticker,
        side=order.side,
        quantity=units,
        order_id=placed.get("brokerage_order_id") or placed.get("order_id"),
        success=True,
        db_session=db,
    )

    return {
        "order": placed,
        "broker": broker,
        "account_id": account_id,
        "universal_symbol_id": universal_symbol_id,
        "units": units,
        "time_in_force": placed.get("time_in_force", time_in_force),
    }


@router.get("/auth/snaptrade/connections")
async def snaptrade_connections_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin/debug endpoint to inspect SnapTrade connections state."""

    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    conns: List[Connection] = (
        db.query(Connection)
        .filter(Connection.broker.in_(BROKER_CONFIG.keys()))
        .order_by(Connection.user_id)
        .all()
    )

    return {
        "connections": [
            {
                "id": c.id,
                "user_id": c.user_id,
                "broker": c.broker,
                "account_type": c.account_type,
                "is_connected": c.is_connected,
                "connection_status": c.connection_status,
                "account_id": c.account_id,
                "updated_at": c.updated_at,
            }
            for c in conns
        ]
    }


def ensure_snaptrade_identity(user: User, db: Session) -> User:
    """register SnapTrade identifiers on first login/creation.

    Only runs once per user; if identifiers already exist, no-op.
    """
    changed = False
    if not user.snaptrade_user_id or not user.snaptrade_token:
        snap_uid, snap_token = register_snaptrade_user(user.id)
        if not user.snaptrade_user_id:
            user.snaptrade_user_id = snap_uid
        if not user.snaptrade_token:
            user.snaptrade_token = snap_token
        changed = True
    if changed:
        user.snaptrade_linked = False  # ensure link flow runs client-side
        user.updated_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post("/auth/register", response_model=Token)
@limiter.limit(RATE_LIMITS["register"])
async def register(request: Request, user_data: UserRegister, db: Session = Depends(get_db)):
    """
    Register a new user
    
    Rate limited: 3 requests per minute per IP
    
    Args:
        user_data: User registration data (email, password, full_name)
        
    Returns:
        JWT access token and user info
    """
    try:
        logger.info(f"Registration attempt for email: {user_data.email}")
        
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == user_data.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Create new user (default role is 'client')
        hashed_password = get_password_hash(user_data.password)
        new_user = User(
            email=user_data.email,
            password_hash=hashed_password,
            full_name=user_data.full_name,
            role="client",
            active=True,
            created_at=datetime.now(timezone.utc)
        )
        
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # Ensure per-broker SnapTrade identities are registered immediately
        ensure_snaptrade_connections(new_user, db)
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.email}, expires_delta=access_token_expires
        )
        
        logger.info(f"User registered successfully: {new_user.email}")
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": new_user.id,
                "email": new_user.email,
                "full_name": new_user.full_name,
                "role": new_user.role,
                "snaptrade_linked": new_user.snaptrade_linked
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration failed: {str(e)}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/auth/login", response_model=Token)
@limiter.limit(RATE_LIMITS["login"])
async def login(request: Request, login_data: UserLogin, db: Session = Depends(get_db)):
    """
    Login with email and password
    
    Rate limited: 5 requests per minute per IP (prevents brute force)
    
    Args:
        login_data: User login data (email and password)
        
    Returns:
        JWT access token and user info
    """
    try:
        logger.info(f"Login attempt for email: {login_data.email}")
        
        # Authenticate user
        user = authenticate_user(db, login_data.email, login_data.password)
        if not user:
            # Audit failed login
            audit_login(
                email=login_data.email,
                success=False,
                request=request,
                failure_reason="Invalid credentials",
                db_session=db,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Block suspended users
        if user.active is False:
            audit_login(
                email=login_data.email,
                success=False,
                request=request,
                user_id=user.id,
                failure_reason="Account suspended",
                db_session=db,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is suspended"
            )

        # First-login registering for per-broker SnapTrade identifiers
        ensure_snaptrade_connections(user, db)

        # First-login bookkeeping and welcome email (sent once)
        now = datetime.now(timezone.utc)
        metadata = user.metadata_json or {}
        welcome_already_sent = bool(metadata.get("welcome_email_sent"))

        # Set first-login timestamp and update last login
        if not user.first_login_at:
            user.first_login_at = now
        user.last_login = now

        # Send welcome email only once per user
        if not welcome_already_sent:
            try:
                await get_email_service().send_welcome_email(
                    user_email=user.email,
                    user_name=user.full_name or user.email.split("@")[0],
                )
                metadata["welcome_email_sent"] = True
            except Exception as email_err:  # noqa: BLE001
                logger.warning("Welcome email send failed for %s: %s", user.email, email_err)

        user.metadata_json = metadata
        db.add(user)
        db.commit()
        db.refresh(user)
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        
        logger.info(f"User logged in successfully: {user.email}")
        
        # Audit successful login
        audit_login(
            email=user.email,
            success=True,
            request=request,
            user_id=user.id,
            db_session=db,
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "snaptrade_linked": user.snaptrade_linked,
                "onboarding_completed": user.onboarding_completed
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )


@router.get("/auth/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current authenticated user information
    
    Returns:
        Current user info
    """
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "snaptrade_linked": current_user.snaptrade_linked,
        "risk_profile": current_user.risk_profile,
        "active": current_user.active,
        "onboarding_completed": current_user.onboarding_completed,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }


@router.post("/auth/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout current user
    
    Note: JWT tokens are stateless, so this is just a placeholder.
    Client should delete the token on their side.
    """
    logger.info(f"User logged out: {current_user.email}")
    return {"message": "Successfully logged out"}


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None


@router.patch("/auth/profile")
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update the current user's profile information.
    
    Only allows updating full_name and email.
    """
    updated = False
    
    if request.full_name is not None and request.full_name != current_user.full_name:
        current_user.full_name = request.full_name
        updated = True
        
    if request.email is not None and request.email != current_user.email:
        # Check if email is already taken
        existing = db.query(User).filter(User.email == request.email, User.id != current_user.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        current_user.email = request.email
        updated = True
    
    if updated:
        current_user.updated_at = datetime.now(timezone.utc)
        db.add(current_user)
        db.commit()
        db.refresh(current_user)
        logger.info(f"User profile updated: {current_user.email}")
    
    return {
        "message": "Profile updated successfully" if updated else "No changes made",
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "role": current_user.role,
        }
    }


@router.post("/auth/onboarding/complete")
async def complete_onboarding(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark the current user's onboarding as complete."""
    current_user.onboarding_completed = True
    current_user.updated_at = datetime.now(timezone.utc)
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    logger.info(f"User completed onboarding: {current_user.email}")
    return {"message": "Onboarding completed", "onboarding_completed": True}
