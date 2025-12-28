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
from app.models.database import SessionLocal, Log, Alert, SystemStatus as SystemStatusModel
import yfinance as yf
from datetime import datetime
import logging
import uuid

logger = logging.getLogger(__name__)


def refresh_market_data():
    """Refresh market data and detect regime changes"""
    
    try:
        db = SessionLocal()
        
        # Refresh market data (if service available)
        refresh_success = False
        if MarketDataService is not None:
            market_data_service = MarketDataService()
            try:
                market_data_service.refresh_market_data()
                refresh_success = True
            except Exception as me:
                logger.warning(f"Market data service failed: {me}")
        else:
            logger.warning("MarketDataService unavailable; skipping market data refresh")

        # Refresh benchmark data (S&P 500 intraday hourly)
        benchmark_success = False
        benchmark_timestamp = None
        try:
            df = yf.download("^GSPC", period="7d", interval="60m", progress=False)
            if df is not None and not df.empty:
                benchmark_timestamp = df.index[-1].to_pydatetime()
                benchmark_success = True
            else:
                logger.warning("Benchmark fetch returned empty dataset")
        except Exception as bench_error:
            logger.warning(f"Benchmark data fetch failed: {bench_error}")
        
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
        
        # Persist market/benchmark data availability state
        try:
            system_status = db.query(SystemStatusModel).filter(SystemStatusModel.id == "system").first()
            if not system_status:
                system_status = SystemStatusModel(id="system")
            system_status.market_data_available = refresh_success
            if refresh_success:
                system_status.last_market_data_refresh = datetime.utcnow()
            system_status.benchmark_data_available = benchmark_success
            if benchmark_success and benchmark_timestamp:
                system_status.last_benchmark_refresh = benchmark_timestamp
            db.add(system_status)
        except Exception as status_error:
            logger.warning(f"Failed to persist market data status: {status_error}")

        # Log the refresh
        log = Log(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            level="info" if refresh_success else "warning",
            message="Market data refreshed successfully" if refresh_success else "Market data refresh skipped or failed",
            component="data_refresh_job",
            metadata_json={
                "regime_changed": regime_changed,
                "latest_regime": latest_regime,
                "refresh_success": refresh_success,
                "benchmark_success": benchmark_success,
            }
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
