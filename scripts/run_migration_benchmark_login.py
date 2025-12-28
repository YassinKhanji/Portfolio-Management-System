from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.models.database import engine

sql_path = Path(__file__).parent / "migrations" / "2025-12-28_benchmark_and_login.sql"
sql = sql_path.read_text()
statements = [s.strip() for s in sql.split(';') if s.strip()]

try:
    with engine.begin() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt)
    print(f"Migration applied: {len(statements)} statements")
except SQLAlchemyError as e:
    print(f"Migration failed: {e}")
    raise
