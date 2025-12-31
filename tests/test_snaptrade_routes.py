import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.database import Base, User, Connection
from app.routers import auth as auth_router
from app.services.snaptrade_integration import SnapTradeClientError


@pytest.fixture
def test_app(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(
        id=str(uuid.uuid4()),
        email="snaptrade@test.com",
        password_hash="test-hash",
        full_name="Snap Trader",
        role="admin",
        active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_current_user():
        session = TestingSessionLocal()
        try:
            return session.get(User, user.id)
        finally:
            session.close()

    app.dependency_overrides[auth_router.get_db] = override_get_db
    app.dependency_overrides[auth_router.get_current_user] = override_current_user

    # Ensure deterministic SnapTrade identity registering
    monkeypatch.setattr(auth_router, "register_snaptrade_user", lambda email: ("user-id", "user-secret"))

    client = TestClient(app)
    try:
        yield client, TestingSessionLocal, user
    finally:
        app.dependency_overrides = {}


def test_accounts_endpoint_persists_account(monkeypatch, test_app):
    client, SessionLocal, user = test_app

    monkeypatch.setattr(auth_router, "list_accounts", lambda uid, secret: [{"id": "acct-123", "currency": "CAD"}])

    resp = client.get("/api/auth/snaptrade/accounts/wealthsimple")
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_account_id"] == "acct-123"

    session = SessionLocal()
    connection = session.query(Connection).filter_by(user_id=user.id, broker="wealthsimple").first()
    assert connection is not None
    assert connection.account_id == "acct-123"
    session.close()


def test_symbol_lookup_returns_universal_id(monkeypatch, test_app):
    client, SessionLocal, user = test_app

    # Pre-create connection with account id
    session = SessionLocal()
    conn = Connection(
        id=str(uuid.uuid4()),
        user_id=user.id,
        snaptrade_user_id="test-user-id",
        snaptrade_user_secret="test-fake-secret-for-unit-tests",  # nosec - test fixture only
        account_type="equities",
        broker="wealthsimple",
        is_connected=True,
        connection_status="connected",
        account_id="acct-abc",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    session.commit()
    session.close()

    monkeypatch.setattr(
        auth_router,
        "get_symbol_quote",
        lambda ticker, account_id, uid, secret: {"price": 10.0, "universal_symbol_id": "sym-123"},
    )

    resp = client.get("/api/auth/snaptrade/symbol/AAPL?broker=wealthsimple")
    assert resp.status_code == 200
    data = resp.json()
    assert data["universal_symbol_id"] == "sym-123"
    assert data["account_id"] == "acct-abc"


def test_order_endpoint_fallbacks_to_ioc(monkeypatch, test_app):
    client, SessionLocal, user = test_app

    session = SessionLocal()
    conn = Connection(
        id=str(uuid.uuid4()),
        user_id=user.id,
        snaptrade_user_id="test-user-id",
        snaptrade_user_secret="test-fake-secret-for-unit-tests",  # nosec - test fixture only
        account_type="equities",
        broker="wealthsimple",
        is_connected=True,
        connection_status="connected",
        account_id="acct-abc",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    session.commit()
    session.close()

    call_count = {"orders": 0}

    def fake_place_equity_order(**kwargs):
        call_count["orders"] += 1
        if call_count["orders"] == 1:
            raise SnapTradeClientError("TIF unsupported")
        return {"id": "order-1", "time_in_force": kwargs.get("time_in_force", "IOC")}

    monkeypatch.setattr(auth_router, "place_equity_order", fake_place_equity_order)
    monkeypatch.setattr(
        auth_router,
        "get_symbol_quote",
        lambda ticker, account_id, uid, secret: {"price": 20.0, "universal_symbol_id": "sym-999"},
    )

    payload = {
        "broker": "wealthsimple",
        "ticker": "AAPL",
        "account_id": "acct-abc",
        "notional": 1000,
        "side": "BUY",
    }
    resp = client.post("/api/auth/snaptrade/orders", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["order"]["time_in_force"].upper() == "IOC"
    assert call_count["orders"] == 2


def test_admin_connections_requires_admin(monkeypatch, test_app):
    client, SessionLocal, user = test_app

    # Set user to client and ensure forbidden
    session = SessionLocal()
    user_record = session.get(User, user.id)
    user_record.role = "client"
    session.commit()
    session.close()

    resp = client.get("/api/auth/snaptrade/connections")
    assert resp.status_code == 403