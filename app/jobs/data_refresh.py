"""
Data Refresh Job

Refreshes market data and regime information.
"""

from app.services.market_data import MarketDataService
from app.trading.regime_detection import RegimeDetectionService
from app.models.database import Session, Log, Alert
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def refresh_market_data():
    """Refresh market data and detect regime changes"""
    
    try:
        db = Session()
        
        # Refresh market data
        market_data_service = MarketDataService()
        market_data_service.refresh_market_data()
        
        # Detect regime changes
        regime_service = RegimeDetectionService()
        regime_changed = regime_service.is_regime_changed()
        
        if regime_changed:
            latest_regime = regime_service.get_latest_regime()
            logger.warning(f"REGIME CHANGE DETECTED: {latest_regime}")
            
            # Create alert for regime change
            alert = Alert(
                alert_type="REGIME_CHANGE",
                severity="HIGH",
                message=f"Market regime changed to {latest_regime}",
                user_id=None,  # System-wide alert
                action_required=True
            )
            db.add(alert)
        
        # Log the refresh
        log = Log(
            timestamp=datetime.utcnow(),
            level="info",
            message="Market data refreshed successfully",
            component="data_refresh_job",
            metadata_json={"regime_changed": regime_changed}
        )
        db.add(log)
        db.commit()
        
        logger.info("Market data refresh completed")
        
    except Exception as e:
        logger.error(f"Data refresh failed: {str(e)}")
        
        # Log the error
        try:
            db = Session()
            log = Log(
                timestamp=datetime.utcnow(),
                level="error",
                message=f"Data refresh failed: {str(e)}",
                component="data_refresh_job"
            )
            db.add(log)
            db.commit()
        except:
            pass
    
    finally:
        try:
            db.close()
        except:
            pass
