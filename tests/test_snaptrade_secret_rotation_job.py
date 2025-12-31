import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, Connection


@pytest.fixture
def testing_sessionlocal():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def test_secret_rotation_updates_all_connections(monkeypatch, testing_sessionlocal):
    # Arrange
    session = testing_sessionlocal()
    snaptrade_user_id = f"snap-user-{uuid.uuid4()}"

    old_secret_1 = f"test-secret-{uuid.uuid4()}"
    old_secret_2 = f"test-secret-{uuid.uuid4()}"
    new_secret = f"rotated-secret-{uuid.uuid4()}"

    c1 = Connection(
        id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        snaptrade_user_id=snaptrade_user_id,
        snaptrade_user_secret=old_secret_1,
        account_type="equities",
        broker="wealthsimple",
        is_connected=True,
        connection_status="connected",
        account_id="acct-1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    c2 = Connection(
        id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        snaptrade_user_id=snaptrade_user_id,
        snaptrade_user_secret=old_secret_2,
        account_type="crypto",
        broker="kraken",
        is_connected=True,
        connection_status="connected",
        account_id="acct-2",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add_all([c1, c2])
    session.commit()
    session.close()

    # Patch job module globals to use test DB + deterministic reset behavior
    from app.jobs import snaptrade_secret_rotation as job

    monkeypatch.setattr(job, "SessionLocal", testing_sessionlocal)
    monkeypatch.setattr(job, "is_emergency_stop_active", lambda: False)

    def fake_reset(user_id: str, user_secret: str):
        # Validate we are called with the expected user id and one of the stored secrets
        assert user_id == snaptrade_user_id
        assert user_secret in {old_secret_1, old_secret_2}
        return user_id, new_secret

    monkeypatch.setattr(job, "reset_snaptrade_user_secret", fake_reset)

    # Act
    counters = job.rotate_snaptrade_user_secrets()

    # Assert
    assert counters["users_seen"] == 1
    assert counters["users_rotated"] == 1
    assert counters["users_failed"] == 0

    verify = testing_sessionlocal()
    conns = verify.query(Connection).filter(Connection.snaptrade_user_id == snaptrade_user_id).all()
    assert len(conns) == 2
    assert {c.snaptrade_user_secret for c in conns} == {new_secret}
    verify.close()
