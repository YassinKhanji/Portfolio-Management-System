"""Check database positions for debugging"""
import sys
sys.path.insert(0, ".")

from app.models.database import SessionLocal, Position

def main():
    db = SessionLocal()
    try:
        positions = db.query(Position).all()
        print(f"Total positions: {len(positions)}")
        for p in positions:
            metadata = p.metadata_json or {}
            asset_class = metadata.get('asset_class', 'N/A')
            broker = metadata.get('broker', 'N/A')
            currency = metadata.get('currency', 'N/A')
            print(f"  {p.symbol}: price={p.price}, cost_basis={p.cost_basis}, class={asset_class}, broker={broker}, currency={currency}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
