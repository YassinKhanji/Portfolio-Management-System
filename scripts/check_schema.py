#!/usr/bin/env python3
"""Check database schema"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

inspector = inspect(engine)
columns = inspector.get_columns('users')

print("Users table columns:")
for col in columns:
    print(f"  - {col['name']}: {col['type']}")
