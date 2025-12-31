import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.database import (
    Alert,
    AlertPreference,
    Base,
    Connection,
    Log,
    PortfolioSnapshot,
    Position,
    RiskProfile,
    Transaction,
    User,
)
from app.routers import portfolio as portfolio_router


@pytest.fixture()
def test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    user_id = str(uuid.uuid4())

    # Seed a user and related rows that should be purged
    db = TestingSessionLocal()
    user = User(
        id=user_id,
        email="cleanup@test.com",
        password_hash="test-hash",
        full_name="Cleanup User",
        role="client",
        active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)

    db.add(
        Position(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol="BTC",
            quantity=1.0,
            price=30000.0,
            market_value=30000.0,
            allocation_percentage=0.5,
            target_percentage=0.5,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        Transaction(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol="BTC",
            quantity=1.0,
            price=30000.0,
            side="BUY",
            snaptrade_order_id="ord-1",
            status="filled",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        Connection(
            id=str(uuid.uuid4()),
            user_id=user_id,
            snaptrade_user_id="test-snap-user",
            snaptrade_user_secret="test-fake-secret-for-unit-tests",  # nosec - test fixture only
            account_type="crypto",
            broker="kraken",
            is_connected=True,
            connection_status="connected",
            account_id="acct-1",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        PortfolioSnapshot(
            id=str(uuid.uuid4()),
            user_id=user_id,
            total_value=30000.0,
            crypto_value=30000.0,
            stocks_value=0.0,
            cash_value=0.0,
            daily_return=0.0,
            daily_return_pct=0.0,
            recorded_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        RiskProfile(
            id=str(uuid.uuid4()),
            user_id=user_id,
            crypto_allocation=0.5,
            stocks_allocation=0.3,
            cash_allocation=0.2,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        Alert(
            alert_type="test",
            severity="info",
            message="test alert",
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        AlertPreference(
            id=str(uuid.uuid4()),
            user_id=user_id,
            rebalance_completed=True,
            regime_change=True,
            emergency_stop=True,
            transfer_needed=True,
            drawdown_warning=True,
            health_check_failed=False,
            api_error=False,
            email_enabled=True,
            daily_digest_enabled=True,
            daily_digest_time="08:00",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        Log(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            level="info",
            message="user log entry",
            component="tests",
            user_id=user_id,
            metadata_json={},
        )
    )

    db.commit()
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[portfolio_router.get_db] = override_get_db
    client = TestClient(app)

    try:
        yield client, TestingSessionLocal, user_id
    finally:
        app.dependency_overrides = {}


def test_delete_client_cleans_related_rows(test_app):
    client, SessionLocal, user_id = test_app

    resp = client.delete(f"/api/clients/{user_id}")
    assert resp.status_code == 204
    assert resp.content == b""

    session = SessionLocal()
    try:
        assert session.query(User).filter_by(id=user_id).count() == 0
        assert session.query(Position).filter_by(user_id=user_id).count() == 0
        assert session.query(Transaction).filter_by(user_id=user_id).count() == 0
        assert session.query(Connection).filter_by(user_id=user_id).count() == 0
        assert session.query(PortfolioSnapshot).filter_by(user_id=user_id).count() == 0
        assert session.query(RiskProfile).filter_by(user_id=user_id).count() == 0
        assert session.query(Alert).filter_by(user_id=user_id).count() == 0
        assert session.query(AlertPreference).filter_by(user_id=user_id).count() == 0
        assert session.query(Log).filter_by(user_id=user_id).count() == 0
    finally:
        session.close()
