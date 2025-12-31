"""
Migration: Encrypt Existing SnapTrade Secrets

This script encrypts any plaintext snaptrade_user_secret values in the database.
Run this ONCE after deploying the encryption feature.

Usage:
    cd Backend
    python -m scripts.migrations.encrypt_secrets
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

# Import encryption helpers
from app.core.security import encrypt_value, is_encrypted

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def migrate_secrets():
    """Encrypt all plaintext snaptrade_user_secret values."""
    
    session = Session()
    
    try:
        # Get all connections
        result = session.execute(text("SELECT id, snaptrade_user_secret FROM connections"))
        connections = result.fetchall()
        
        encrypted_count = 0
        already_encrypted = 0
        failed = 0
        
        for conn_id, secret in connections:
            if not secret:
                continue
                
            if is_encrypted(secret):
                already_encrypted += 1
                continue
            
            try:
                encrypted_secret = encrypt_value(secret)
                session.execute(
                    text("UPDATE connections SET snaptrade_user_secret = :secret WHERE id = :id"),
                    {"secret": encrypted_secret, "id": conn_id}
                )
                encrypted_count += 1
                print(f"✅ Encrypted secret for connection {conn_id}")
            except Exception as e:
                failed += 1
                print(f"❌ Failed to encrypt connection {conn_id}: {e}")
        
        session.commit()
        
        print("\n" + "=" * 50)
        print("MIGRATION COMPLETE")
        print("=" * 50)
        print(f"  Encrypted:         {encrypted_count}")
        print(f"  Already encrypted: {already_encrypted}")
        print(f"  Failed:            {failed}")
        print(f"  Total connections: {len(connections)}")
        
    except Exception as e:
        session.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 50)
    print("SNAPTRADE SECRET ENCRYPTION MIGRATION")
    print("=" * 50)
    print()
    
    # Verify ENCRYPTION_KEY is set
    if not os.getenv("ENCRYPTION_KEY"):
        print("⚠️  WARNING: ENCRYPTION_KEY not set!")
        print("   Using development fallback key.")
        print("   Set ENCRYPTION_KEY in production!")
        print()
    
    response = input("Continue with migration? (yes/no): ")
    if response.lower() != "yes":
        print("Aborted.")
        sys.exit(0)
    
    migrate_secrets()
