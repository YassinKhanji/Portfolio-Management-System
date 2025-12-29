#!/usr/bin/env python3
"""
Script to add a new user to the database

Usage:
    python scripts/add_user.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, Column, String, DateTime, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import uuid
from passlib.context import CryptContext

# Password hashing context - using pbkdf2 instead of bcrypt to avoid version issues
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """Generate password hash"""
    return pwd_context.hash(password)

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/portfolio_management")

# Create engine and session
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define User model inline to avoid import issues
class User(Base):
    """User account and preferences"""
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
    rebalance_frequency = Column(String, default="daily")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    metadata_json = Column(JSON, default={})

def add_user(email: str, password: str, full_name: str, role: str = "client", risk_profile: str = "Balanced"):
    """Add a new user to the database
    
    Args:
        email: User email address
        password: User password (will be hashed)
        full_name: User's full name
        role: User role - 'client' or 'admin' (default: 'client')
        risk_profile: Investment risk profile (default: 'Balanced')
    """
    
    # Create database tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            print(f"❌ User with email '{email}' already exists!")
            return False
        
        # Create new user
        user_id = str(uuid.uuid4())
        hashed_password = get_password_hash(password)
        
        new_user = User(
            id=user_id,
            email=email,
            password_hash=hashed_password,
            full_name=full_name,
            role=role,
            active=True,
            risk_profile=risk_profile,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        print(f"✅ User created successfully!")
        print(f"   Email: {new_user.email}")
        print(f"   Name: {new_user.full_name}")
        print(f"   Role: {new_user.role}")
        print(f"   ID: {new_user.id}")
        print(f"   Risk Profile: {new_user.risk_profile}")
        print(f"   Created: {new_user.created_at}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating user: {str(e)}")
        db.rollback()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    # User details for client
    email_client = "yassinkhanji9@gmail.com"
    password_client = "Yassin2002"[:72]  # Trim to 72 bytes max for bcrypt
    full_name_client = "Yassin Khanji"
    risk_profile = "Balanced"
    
    # User details for admin
    email_admin = "yassinkhanji9@gmail.com"
    password_admin = "Yassin2002"[:72]
    full_name_admin = "Yassin Khanji"
    
    print("=" * 60)
    print("ADDING CLIENT USER")
    print("=" * 60)
    print(f"Email: {email_client}")
    print(f"Name: {full_name_client}")
    print(f"Risk Profile: {risk_profile}")
    print()
    
    success_client = add_user(email_client, password_client, full_name_client, "client", risk_profile)
    
    print()
    print("=" * 60)
    print("ADDING ADMIN USER")
    print("=" * 60)
    print(f"Email: {email_admin}")
    print(f"Name: {full_name_admin}")
    print()
    
    success_admin = add_user(email_admin, password_admin, full_name_admin, "admin", risk_profile)
    
    print()
    if success_client:
        print("✨ Client user added successfully!")
    else:
        print("⚠️ Failed to add client user (may already exist)")
    
    if success_admin:
        print("✨ Admin user added successfully!")
    else:
        print("⚠️ Failed to add admin user (may already exist)")
