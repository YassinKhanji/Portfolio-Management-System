#!/usr/bin/env python3
"""
Promote or create the owner account.

Usage:
    python scripts/make_owner.py [email] [password]

Defaults:
    - Email: from ADMIN_EMAIL env var (required if not passed)
    - Password: from OWNER_PASSWORD env var, else prompts if missing

Behavior:
    - If a user with the email exists, updates role to "owner", sets active=True.
    - If missing, creates the user with provided password and role "owner".
"""
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, DateTime, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext

# Load environment variables from .env
load_dotenv()

# Add project root to path for consistency
sys.path.insert(0, str(Path(__file__).parent.parent))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/portfolio_management")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "") or None

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="client")
    snaptrade_token = Column(String, nullable=True)
    snaptrade_user_id = Column(String, nullable=True)
    snaptrade_linked = Column(Boolean, default=False)
    risk_profile = Column(String, default="Balanced")
    rebalance_frequency = Column(String, default="weekly")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    metadata_json = Column(JSON, default={})


def ensure_owner(email: str, password: str) -> None:
    email = email.strip().lower()
    if not email:
        raise SystemExit("ADMIN_EMAIL is required (env or argument).")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        now = datetime.utcnow()

        if user:
            user.role = "owner"
            user.active = True
            user.updated_at = now
            if password:
                user.password_hash = pwd_context.hash(password)
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"✅ Updated existing user to owner: {user.email}")
        else:
            if not password:
                raise SystemExit("Password required to create owner user.")
            new_user = User(
                id=os.getenv("OWNER_USER_ID", None) or __import__("uuid").uuid4().hex,
                email=email,
                password_hash=pwd_context.hash(password),
                full_name=os.getenv("OWNER_FULL_NAME", "Owner"),
                role="owner",
                active=True,
                risk_profile="Balanced",
                created_at=now,
                updated_at=now,
            )
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            print(f"✅ Created owner user: {new_user.email}")
    finally:
        db.close()


def main():
    email = (sys.argv[1] if len(sys.argv) > 1 else ADMIN_EMAIL).strip().lower()
    password = sys.argv[2] if len(sys.argv) > 2 else OWNER_PASSWORD
    # If password is empty and user exists, the script will keep the existing hash
    ensure_owner(email, password)


if __name__ == "__main__":
    main()
