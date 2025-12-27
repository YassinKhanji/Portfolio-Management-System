#!/usr/bin/env python3
"""
Script to update an existing user's role to admin

Usage:
    python scripts/make_admin.py
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

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/portfolio_management")

# Create engine and session
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define User model inline
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
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    metadata_json = Column(JSON, default={})


def make_admin(email: str):
    """Update user role to admin"""
    
    db = SessionLocal()
    
    try:
        # Find user by email
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"❌ User with email '{email}' not found!")
            return False
        
        # Update role to admin
        old_role = user.role
        user.role = "admin"
        db.commit()
        db.refresh(user)
        
        print(f"✅ User updated successfully!")
        print(f"   Email: {user.email}")
        print(f"   Name: {user.full_name}")
        print(f"   Old Role: {old_role}")
        print(f"   New Role: {user.role}")
        print(f"   Updated: {user.updated_at}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error updating user: {str(e)}")
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    email = "yassinkhanji9@gmail.com"
    
    print(f"Updating user to admin...")
    print(f"Email: {email}")
    print()
    
    success = make_admin(email)
    
    if success:
        print("\n✨ User role updated to admin successfully!")
    else:
        print("\n⚠️ Failed to update user role")
        sys.exit(1)
