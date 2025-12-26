"""
Web App API Client for Trading System

This module provides the interface between the React frontend (Portfolio Management Dashboard)
and the Python Trading System backend.

Usage:
    # In your Node.js/Express backend or directly in React
    client = TradingSystemClient(base_url="http://localhost:8000")
    
    # Trigger rebalance for user
    response = await client.rebalance_user(user_id="user123")
    
    # Check system status
    status = await client.get_system_health()
"""

import httpx
import logging
from typing import Optional, Dict, List
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ============================================================================
# Response Models (matching api.py)
# ============================================================================

@dataclass
class RegimeStatus:
    """Current market regime"""
    season: str  # "BULL", "BEAR", "SIDEWAYS", "HODL"
    vol_regime: int  # 0, 1, 2
    dir_regime: int  # 0, 1, 2
    confidence: float
    btc_season: str
    eth_season: str
    timestamp: datetime


@dataclass
class SystemHealthStatus:
    """System health check response"""
    status: str  # "healthy", "degraded", "critical"
    regime_engine: bool
    database_connection: bool
    market_data_age_minutes: int
    total_users: int
    active_users: int
    total_aum: float
    emergency_stop: bool
    last_rebalance: Optional[datetime]


@dataclass
class LogEntry:
    """Log entry from trading system"""
    timestamp: datetime
    level: str  # debug, info, warning, error, critical
    message: str
    component: str


@dataclass
class AlertMessage:
    """Alert from trading system"""
    id: str
    type: str  # rebalance_needed, trade_failed, regime_change, drift_alert, data_refresh_failed
    severity: str  # info, warning, critical
    message: str
    created_at: datetime
    read: bool


# ============================================================================
# Trading System API Client
# ============================================================================

class TradingSystemClient:
    """
    Python/TypeScript-agnostic client for Trading System API
    
    Usage in Express backend:
        const client = new TradingSystemClient("http://localhost:8000");
        const regime = await client.getRegimeStatus();
        
    Usage in Python:
        client = TradingSystemClient("http://localhost:8000")
        regime = client.get_regime_status()
    """
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: Optional[str] = None):
        """
        Initialize API client
        
        Args:
            base_url: Trading System base URL
            api_key: Optional API key for authentication (implement in production)
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
    
    # ========================================================================
    # Rebalancing Endpoints
    # ========================================================================
    
    async def rebalance_user(
        self,
        user_id: str,
        force: bool = False,
        dry_run: bool = False
    ) -> Dict:
        """
        Trigger rebalancing for single user
        
        Args:
            user_id: User ID
            force: Force rebalance even if no drift
            dry_run: Calculate but don't execute trades
        
        Returns:
            {
                "status": "queued",
                "user_id": "user123",
                "estimated_completion": "2024-01-15T10:30:00Z"
            }
        """
        url = f"{self.base_url}/api/rebalance/{user_id}"
        
        payload = {
            "force": force,
            "dry_run": dry_run
        }
        
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Rebalance request failed: {e}")
            raise
    
    async def rebalance_all_users(self, dry_run: bool = False) -> Dict:
        """
        Trigger rebalancing for ALL users (admin endpoint)
        
        Args:
            dry_run: Calculate but don't execute
        
        Returns:
            {
                "status": "queued",
                "users_count": 42,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        """
        url = f"{self.base_url}/api/rebalance/all"
        
        payload = {"dry_run": dry_run}
        
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Bulk rebalance request failed: {e}")
            raise
    
    async def calculate_portfolio(self, user_id: str) -> Dict:
        """
        Calculate target allocation WITHOUT executing trades
        
        Returns:
            {
                "user_id": "user123",
                "btc_target": 0.40,
                "eth_target": 0.25,
                "alt_target": 0.20,
                "stable_target": 0.15,
                "total_value": 100000.00,
                "current_regime": "BULL"
            }
        """
        url = f"{self.base_url}/api/portfolio/calculate/{user_id}"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Portfolio calculation failed: {e}")
            raise
    
    # ========================================================================
    # Regime & Status Endpoints
    # ========================================================================
    
    async def get_regime_status(self) -> Dict:
        """
        Get current market regime
        
        Returns:
            {
                "season": "BULL",
                "vol_regime": 2,
                "dir_regime": 1,
                "confidence": 0.85,
                "btc_season": "BULL",
                "eth_season": "SIDEWAYS",
                "timestamp": "2024-01-15T10:00:00Z"
            }
        """
        url = f"{self.base_url}/api/regime/status"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Regime status request failed: {e}")
            raise
    
    async def get_system_health(self) -> Dict:
        """
        Get system health status
        
        Returns:
            {
                "status": "healthy",
                "regime_engine": true,
                "database_connection": true,
                "market_data_age_minutes": 15,
                "total_users": 42,
                "active_users": 35,
                "total_aum": 5234567.89,
                "emergency_stop": false,
                "last_rebalance": "2024-01-15T09:30:00Z"
            }
        """
        url = f"{self.base_url}/api/system/health"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"System health request failed: {e}")
            raise
    
    # ========================================================================
    # Monitoring Endpoints
    # ========================================================================
    
    async def get_logs(
        self,
        level: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Get system logs
        
        Args:
            level: Filter by log level (debug, info, warning, error, critical)
            limit: Max number of logs to return
        
        Returns:
            List of log entries
        """
        url = f"{self.base_url}/api/system/logs"
        
        params = {"limit": limit}
        if level:
            params["level"] = level
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Logs request failed: {e}")
            raise
    
    async def get_alerts(
        self,
        unread_only: bool = False,
        limit: int = 50
    ) -> List[Dict]:
        """
        Get system alerts
        
        Args:
            unread_only: Only unread alerts
            limit: Max number of alerts
        
        Returns:
            List of alert messages
        """
        url = f"{self.base_url}/api/system/alerts"
        
        params = {
            "unread_only": unread_only,
            "limit": limit
        }
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Alerts request failed: {e}")
            raise
    
    # ========================================================================
    # Emergency Controls
    # ========================================================================
    
    async def emergency_stop(self, reason: str = "Manual admin stop") -> Dict:
        """
        Emergency stop all trading (admin only)
        
        Args:
            reason: Reason for emergency stop
        
        Returns:
            {"status": "stopped", "reason": "..."}
        """
        url = f"{self.base_url}/api/system/emergency-stop"
        
        payload = {"reason": reason}
        
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Emergency stop failed: {e}")
            raise
    
    async def emergency_stop_reset(self) -> Dict:
        """
        Reset emergency stop (admin only)
        
        Returns:
            {"status": "resumed", "timestamp": "..."}
        """
        url = f"{self.base_url}/api/system/emergency-stop/reset"
        
        try:
            response = await self.client.post(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Emergency stop reset failed: {e}")
            raise


# ============================================================================
# Express.js Middleware Example
# ============================================================================

"""
// middleware.js
import axios from 'axios';

const TRADING_SYSTEM_BASE = process.env.TRADING_SYSTEM_URL || "http://localhost:8000";

// Create axios instance
export const tradingSystemAPI = axios.create({
  baseURL: TRADING_SYSTEM_BASE,
  timeout: 10000,
});

// Error handling middleware
tradingSystemAPI.interceptors.response.use(
  response => response.data,
  error => {
    console.error("Trading System API Error:", error);
    throw {
      status: error.response?.status,
      message: error.response?.data?.detail || error.message
    };
  }
);

// Usage in Express routes:
export const getTradingSystemStatus = async (req, res) => {
  try {
    const status = await tradingSystemAPI.get('/api/system/health');
    res.json(status);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};
"""


# ============================================================================
# React Hook Example
# ============================================================================

"""
// useTrading.ts
import { useCallback, useState } from 'react';

const TRADING_API_URL = process.env.REACT_APP_TRADING_URL || 'http://localhost:8000';

export const useTrading = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rebalanceUser = useCallback(async (userId: string, force = false) => {
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`${TRADING_API_URL}/api/rebalance/${userId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force })
      });
      
      if (!response.ok) throw new Error('Rebalance failed');
      
      const data = await response.json();
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const getSystemStatus = useCallback(async () => {
    try {
      const response = await fetch(`${TRADING_API_URL}/api/system/health`);
      if (!response.ok) throw new Error('Failed to fetch status');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    }
  }, []);

  return { loading, error, rebalanceUser, getSystemStatus };
};

// Usage in React component:
export const AdminDashboard = () => {
  const { rebalanceUser, getSystemStatus } = useTrading();

  const handleRebalance = async () => {
    const result = await rebalanceUser('user123', true);
    console.log('Rebalance queued:', result);
  };

  return <button onClick={handleRebalance}>Rebalance Now</button>;
};
"""

if __name__ == "__main__":
    # Example usage available in tests/test_integration.py
    pass
