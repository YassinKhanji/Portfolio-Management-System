"""
Fix cost_basis for crypto positions

The cost_basis for Kraken crypto positions was stored in USD instead of CAD.
This script converts them to CAD.
"""
import sys
sys.path.insert(0, ".")

from app.models.database import SessionLocal, Position
from app.core.currency import convert_to_cad

def main():
    db = SessionLocal()
    try:
        # Find crypto positions with cost_basis that need conversion
        positions = db.query(Position).filter(Position.cost_basis.isnot(None)).all()
        
        print(f"Found {len(positions)} positions with cost_basis")
        
        updated = 0
        for p in positions:
            metadata = p.metadata_json or {}
            asset_class = metadata.get('asset_class', '').lower()
            
            # Only convert crypto positions (Kraken = USD)
            if asset_class == 'crypto':
                old_cost = p.cost_basis
                # Convert from USD to CAD
                new_cost = convert_to_cad(old_cost, "USD")
                
                print(f"  {p.symbol}: cost_basis ${old_cost} USD -> ${new_cost:.6f} CAD")
                p.cost_basis = new_cost
                updated += 1
        
        if updated > 0:
            print(f"\nCommitting {updated} updates...")
            db.commit()
            print("Done!")
        else:
            print("\nNo crypto positions to update")
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
