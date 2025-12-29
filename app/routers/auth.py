"""
Authentication Router

Handles user authentication with JWT tokens.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import uuid
import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import logging

from ..models.database import SessionLocal, User, Connection
from ..services.snaptrade_integration import (
    register_snaptrade_user,
    build_connect_url,
    list_accounts,
    get_symbol_quote,
    place_equity_order,
    SnapTradeClientError,
)
from ..services.email_service import get_email_service
import os
from ..core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])
settings = get_settings()
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

# JWT settings
SECRET_KEY = settings.JWT_SECRET_KEY if hasattr(settings, 'JWT_SECRET_KEY') else "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Models
class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict


class UserRegister(BaseModel):
    email: str
    password: str
    full_name: str


class UserLogin(BaseModel):
    email: str
    password: str


class OrderRequest(BaseModel):
    broker: str
    ticker: Optional[str] = None
    universal_symbol_id: Optional[str] = None
    account_id: Optional[str] = None
    notional: Optional[float] = None
    units: Optional[float] = None
    side: str
    order_type: str = "market"
    time_in_force: str = "DAY"
    limit_price: Optional[float] = None


def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_snaptrade_connections(user: User, db: Session) -> dict:
    """Ensure per-broker SnapTrade identities exist (trade-capable by default).

    Creates/repairs Connection rows for Kraken (crypto) and Wealthsimple (equities), each with its own
    SnapTrade userId/userSecret. Returns a mapping broker -> Connection.
    """

    changed = False
    now = datetime.now(timezone.utc)

    # Reuse a single SnapTrade user for all broker connections; register once if none exist.
    existing_conn = (
        db.query(Connection)
        .filter(Connection.user_id == user.id)
        .first()
    )
    shared_uid = existing_conn.snaptrade_user_id if existing_conn else None
    shared_secret = existing_conn.snaptrade_user_secret if existing_conn else None
    if not shared_uid or not shared_secret:
        shared_uid, shared_secret = register_snaptrade_user(user.id)

    for broker, cfg in BROKER_CONFIG.items():
        conn = (
            db.query(Connection)
            .filter(Connection.user_id == user.id, Connection.broker == broker)
            .first()
        )

        if not conn:
            conn = Connection(
                id=str(uuid.uuid4()),
                user_id=user.id,
                snaptrade_user_id=shared_uid,
                snaptrade_user_secret=shared_secret,
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
            # Repair missing credentials if needed
            if not conn.snaptrade_user_id or not conn.snaptrade_user_secret:
                if not conn.snaptrade_user_id:
                    conn.snaptrade_user_id = shared_uid
                if not conn.snaptrade_user_secret:
                    conn.snaptrade_user_secret = shared_secret
                conn.connection_status = conn.connection_status or "pending"
                conn.updated_at = now
                changed = True

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

    connect_url = build_connect_url(
        user_id=connection.snaptrade_user_id,
        user_secret=connection.snaptrade_user_secret,
        broker=broker,
        connection_type="trade",
    )

    return {
        "connect_url": connect_url,
        "broker": broker,
        "connection_type": "trade",
        "connection_id": connection.id,
        "snaptrade_user_id": connection.snaptrade_user_id,
    }


@router.get("/auth/snaptrade/callback")
async def snaptrade_callback(
    broker: str,
    code: str = "",  # placeholder for future validation
    userId: str = "",
    userSecret: str = "",
    accountId: str = "",
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Handle SnapTrade redirect and mark the broker connection as linked.

    The SnapTrade redirect does not include an Authorization header. We accept
    either a Bearer token (preferred) or the userId/userSecret pair provided by
    SnapTrade and validate it against the stored Connection before linking.
    """

    broker = broker.lower()
    if broker not in BROKER_CONFIG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported broker")

    # Resolve the connection either from the authenticated user or from the query params
    connection: Optional[Connection] = None
    if current_user:
        connections = ensure_snaptrade_connections(current_user, db)
        connection = connections.get(broker)
    elif userId and userSecret:
        connection = (
            db.query(Connection)
            .filter(
                Connection.broker == broker,
                Connection.snaptrade_user_id == userId,
                Connection.snaptrade_user_secret == userSecret,
            )
            .first()
        )
        current_user = connection.user if connection else None

    if not connection or not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to validate SnapTrade connection")

    if userId and userId != connection.snaptrade_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User mismatch")
    if userSecret and userSecret != connection.snaptrade_user_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Secret mismatch")

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

    return {
        "message": f"SnapTrade connection linked for {broker}",
        "broker": broker,
        "connection_id": connection.id,
        "account_id": connection.account_id,
        "snaptrade_linked": current_user.snaptrade_linked,
    }


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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Persist first account id if not already stored
    if accounts and not connection.account_id:
        first_id = accounts[0].get("id") or accounts[0].get("account_id")
        if first_id:
            connection.account_id = first_id
            connection.updated_at = datetime.now(timezone.utc)
            db.add(connection)
            db.commit()

    return {"accounts": accounts, "saved_account_id": connection.account_id}


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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

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
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if not universal_symbol_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Universal symbol id not resolved")

    units = order.units
    if not units:
        try:
            quote = quote or get_symbol_quote(
                order.ticker, account_id, connection.snaptrade_user_id, connection.snaptrade_user_secret
            )
        except SnapTradeClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        else:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

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
async def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """
    Register a new user
    
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
async def login(login_data: UserLogin, db: Session = Depends(get_db)):
    """
    Login with email and password
    
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
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # First-login registering for per-broker SnapTrade identifiers
        ensure_snaptrade_connections(user, db)

        # First-login bookkeeping and welcome email (sent once)
        now = datetime.now(timezone.utc)
        metadata = user.metadata_json or {}
        welcome_already_sent = bool(metadata.get("welcome_email_sent"))

        # Block suspended users
        if user.active is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is suspended"
            )

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
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "snaptrade_linked": user.snaptrade_linked
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
