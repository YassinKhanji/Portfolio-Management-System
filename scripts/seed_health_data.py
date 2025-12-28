"""Seed demo data for Return Health page.

This script creates a demo client, positions, and historical portfolio snapshots
so the /api/portfolio/health-metrics endpoint returns meaningful data.

Usage (from Backend directory with virtualenv active):
  python -m scripts.seed_health_data
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.models.database import SessionLocal, User, Position, PortfolioSnapshot  # type: ignore
from app.routers.auth import get_password_hash  # type: ignore


DEMO_EMAIL = "demo.investor@example.com"
DEMO_PASSWORD = "DemoPass123!"


def upsert_demo_user(session):
    user = session.query(User).filter(User.email == DEMO_EMAIL).first()
    if user:
        return user

    user = User(
        id=str(uuid.uuid4()),
        email=DEMO_EMAIL,
        password_hash=get_password_hash(DEMO_PASSWORD),
        full_name="Demo Investor",
        role="client",
        active=True,
        created_at=datetime.utcnow() - timedelta(days=200),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_positions(session, user_id: str):
    session.query(Position).filter(Position.user_id == user_id).delete()
    session.commit()

    positions = [
        {"symbol": "AAPL", "quantity": 50, "price": 190.0, "metadata_json": {"asset_class": "equity"}},
        {"symbol": "QQQ", "quantity": 40, "price": 400.0, "metadata_json": {"asset_class": "equity"}},
        {"symbol": "BTC", "quantity": 0.8, "price": 48000.0, "metadata_json": {"asset_class": "crypto"}},
        {"symbol": "USDC", "quantity": 5000, "price": 1.0, "metadata_json": {"asset_class": "cash"}},
    ]

    total_value = 0.0
    for p in positions:
        market_value = p["quantity"] * p["price"]
        total_value += market_value
        session.add(Position(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol=p["symbol"],
            quantity=p["quantity"],
            price=p["price"],
            market_value=market_value,
            allocation_percentage=0.0,
            target_percentage=0.0,
            metadata_json=p.get("metadata_json", {}),
        ))

    session.commit()
    return total_value


def reset_snapshots(session, user_id: str, start_value: float = 100000.0, days: int = 180):
    session.query(PortfolioSnapshot).filter(PortfolioSnapshot.user_id == user_id).delete()
    session.commit()

    start_date = datetime.utcnow() - timedelta(days=days)
    snapshots = []
    for i in range(days + 1):
        # gentle upward drift with small wiggle
        growth = 0.0009 * i
        noise = ((i % 15) - 7) * 20  # bounded small noise
        total = start_value * (1 + growth) + noise
        snapshots.append(PortfolioSnapshot(
            id=str(uuid.uuid4()),
            user_id=user_id,
            total_value=round(total, 2),
            crypto_value=round(total * 0.25, 2),
            stocks_value=round(total * 0.65, 2),
            cash_value=round(total * 0.10, 2),
            recorded_at=start_date + timedelta(days=i),
            positions_snapshot={},
            allocation_snapshot={
                "crypto": 0.25,
                "stocks": 0.65,
                "cash": 0.10,
            },
        ))

    session.bulk_save_objects(snapshots)
    session.commit()


def main():
    session = SessionLocal()
    try:
        user = upsert_demo_user(session)
        reset_positions(session, user.id)
        reset_snapshots(session, user.id)
        print("Seed complete. User:", DEMO_EMAIL)
        print("Password:", DEMO_PASSWORD)
    finally:
        session.close()


if __name__ == "__main__":
    main()
