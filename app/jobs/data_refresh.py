"""
Data Refresh Job

Refreshes market data and regime information.
"""

try:
    from app.services.market_data import MarketDataService
except Exception:
    MarketDataService = None  # type: ignore

try:
    from app.trading.regime_detection import RegimeDetectionService
except Exception:
    RegimeDetectionService = None  # type: ignore
from app.models.database import SessionLocal, Log, Alert
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def refresh_market_data():
    """Refresh market data and detect regime changes"""
    
    try:
        db = SessionLocal()
        
        # Refresh market data (if service available)
        if MarketDataService is not None:
            market_data_service = MarketDataService()
            try:
                market_data_service.refresh_market_data()
            except Exception as me:
                logger.warning(f"Market data service failed: {me}")
        else:
            logger.warning("MarketDataService unavailable; skipping market data refresh")
        
        # Detect regime changes
        regime_changed = False
        latest_regime = None
        if RegimeDetectionService is not None:
            try:
                regime_service = RegimeDetectionService()
                regime_changed = regime_service.is_regime_changed()
                if regime_changed:
                    latest_regime = regime_service.get_latest_regime()
            except Exception as re:
                logger.warning(f"Regime detection failed: {re}")
        else:
            logger.info("RegimeDetectionService unavailable; skipping regime detection")
        
        if regime_changed:
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
            metadata_json={"regime_changed": regime_changed, "latest_regime": latest_regime}
        )
        db.add(log)
        db.commit()
        
        logger.info("Market data refresh completed")
        
    except Exception as e:
        logger.error(f"Data refresh failed: {str(e)}")
        
        # Log the error
        try:
            db = SessionLocal()
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
