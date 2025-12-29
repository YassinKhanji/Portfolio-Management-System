"""
SnapTrade Integration Layer (Updated for SDK 11.x)

Abstraction over SnapTrade Python SDK for:
- Account and holdings management
- Order execution
- Performance tracking
- Connection flow

SnapTrade handles investment accounts with multi-broker support.
Uses the official snaptrade-python-sdk package.
"""

import logging
import os
import uuid
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass

from snaptrade_client import SnapTrade

from ..core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SnapTradeClientError(Exception):
    """SnapTrade API error"""
    pass


@dataclass
class Account:
    """SnapTrade account representation"""
    id: str
    name: str
    broker: str
    currency: str
    type: str  # TFSA, RRSP, etc.
    balance: float
    buying_power: float


@dataclass
class Holding:
    """Individual security holding"""
    symbol: str
    name: str
    quantity: float
    price: float
    market_value: float
    currency: str
    percent_of_portfolio: float


@dataclass
class TradeOrder:
    """Trade execution result"""
    order_id: str
    symbol: str
    quantity: float
    price: float
    side: str  # "BUY" or "SELL"
    status: str
    timestamp: datetime


def get_snaptrade_client() -> SnapTrade:
    """Initialize and return a SnapTrade client with credentials from settings."""
    if not settings.SNAPTRADE_CLIENT_ID or not settings.SNAPTRADE_CLIENT_SECRET:
        raise SnapTradeClientError(
            "SnapTrade credentials missing; set SNAPTRADE_CLIENT_ID and SNAPTRADE_CLIENT_SECRET"
        )
    
    return SnapTrade(
        consumer_key=settings.SNAPTRADE_CLIENT_SECRET,
        client_id=settings.SNAPTRADE_CLIENT_ID
    )


def register_snaptrade_user(user_id: str) -> Dict[str, str]:
    """
    Register a new SnapTrade user.

    If the ID already exists on SnapTrade (code 1010), retry once with a
    suffixed UUID to avoid collisions while still persisting the credentials we get.
    """

    def _attempt(uid: str) -> Dict[str, str]:
        snaptrade = get_snaptrade_client()
        response = snaptrade.authentication.register_snap_trade_user(user_id=uid)
        return {
            "userId": response.body.get("userId"),
            "userSecret": response.body.get("userSecret"),
        }

    try:
        return _attempt(user_id)
    except Exception as e:
        msg = str(e) if e else ""
        # SnapTrade 1010: user already exists
        if "already exist" in msg or "1010" in msg:
            fallback_id = f"{user_id}-{uuid.uuid4().hex[:8]}"
            try:
                logger.warning("SnapTrade userId %s exists; retrying with %s", user_id, fallback_id)
                return _attempt(fallback_id)
            except Exception as e2:
                logger.error("Fallback SnapTrade registration failed for %s: %s", fallback_id, e2)
                raise SnapTradeClientError(f"User registration failed after fallback: {e2}")
        logger.error(f"Failed to register SnapTrade user {user_id}: {e}")
        raise SnapTradeClientError(f"User registration failed: {e}")


def generate_connection_url(
    user_id: str,
    user_secret: str,
    broker: Optional[str] = None,
    immediate_redirect: bool = True,
    custom_redirect: Optional[str] = None,
    reconnect: Optional[str] = None,
    connection_type: str = "trade",
) -> str:
    """
    Generate a connection portal URL for a user to connect their brokerage account.
    
    Args:
        user_id: SnapTrade user ID
        user_secret: SnapTrade user secret
        broker: Optional broker slug to pre-select (e.g., "QUESTRADE", "WEALTHSIMPLE", "ALPACA")
        immediate_redirect: Whether to bypass broker selection screen if possible
        custom_redirect: Custom redirect URI after connection
        reconnect: Brokerage authorization ID to reconnect
        connection_type: Type of connection ("read", "trade", or "trade-if-available")
        show_close_button: Controls whether the close (X) button is displayed
        dark_mode: Enable dark mode for the connection portal
        connection_portal_version: Portal version (default "v4")
    
    Returns:
        URL string for the connection portal
    """
    try:
        snaptrade = get_snaptrade_client()
        
        # Build kwargs, only include non-None values
        kwargs = {
            "user_id": user_id,
            "user_secret": user_secret,
        }
        
        if broker is not None:
            kwargs["broker"] = broker
        if immediate_redirect is not None:
            kwargs["immediate_redirect"] = immediate_redirect
        if custom_redirect is not None:
            kwargs["custom_redirect"] = custom_redirect
        elif settings.SNAPTRADE_REDIRECT_URI:
            kwargs["custom_redirect"] = settings.SNAPTRADE_REDIRECT_URI
        if reconnect is not None:
            kwargs["reconnect"] = reconnect
        if connection_type is not None:
            kwargs["connection_type"] = connection_type
        
        response = snaptrade.authentication.login_snap_trade_user(**kwargs)
        
        redirect_uri = response.body.get("redirectURI")
        if not redirect_uri:
            raise SnapTradeClientError("No redirectURI returned from API")
        
        logger.info(f"Generated connection URL for user: {user_id}")
        return redirect_uri
        
    except Exception as e:
        logger.error(f"Failed to generate connection URL: {e}")
        raise SnapTradeClientError(f"Connection URL generation failed: {e}")


# ---------------------------------------------------------------------------
# Compatibility helpers expected by router layer
# ---------------------------------------------------------------------------

def provision_snaptrade_user(user_email: str) -> tuple[str, str]:
    """Provision a SnapTrade user using the email as stable identifier."""
    result = register_snaptrade_user(user_email)
    return result["userId"], result["userSecret"]


def build_connect_url(
    user_id: str,
    user_secret: str,
    broker: str | None = None,
    connection_type: str = "trade",
) -> str:
    """Thin wrapper to generate a SnapTrade connect URL."""
    return generate_connection_url(
        user_id=user_id,
        user_secret=user_secret,
        broker=broker,
        connection_type=connection_type,
        dark_mode=True,
    )


class SnapTradeClient:
    """
    SnapTrade API client for account and trade management.
    Wrapper around the official snaptrade-python-sdk.
    """
    
    def __init__(self, user_id: str, user_secret: str):
        """
        Initialize SnapTrade client for a specific user.
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
        """
        self.user_id = user_id
        self.user_secret = user_secret
        self.client = get_snaptrade_client()
    
    # ========================================================================
    # Account Methods
    # ========================================================================
    
    def get_accounts(self) -> List[Account]:
        """
        Get all user accounts.
        
        Returns:
            List of Account objects
        """
        try:
            response = self.client.account_information.list_user_accounts(
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            accounts = []
            for account_data in response.body:
                account = Account(
                    id=account_data.get("id", ""),
                    name=account_data.get("name", ""),
                    broker=account_data.get("institution_name", ""),
                    currency=account_data.get("currency", "CAD"),
                    type=account_data.get("meta", {}).get("type", ""),
                    balance=float(account_data.get("balance", {}).get("total", 0)),
                    buying_power=float(account_data.get("cash_restrictions", [{}])[0].get("buying_power", {}).get("amount", 0))
                )
                accounts.append(account)
            
            logger.info(f"Retrieved {len(accounts)} accounts for user {self.user_id}")
            return accounts
        
        except Exception as e:
            logger.error(f"Failed to get accounts: {e}")
            raise SnapTradeClientError(f"Failed to get accounts: {e}")
    
    def get_account(self, account_id: str) -> Optional[Account]:
        """Get single account details by ID."""
        accounts = self.get_accounts()
        for account in accounts:
            if account.id == account_id:
                return account
        return None
    
    # ========================================================================
    # Holdings Methods
    # ========================================================================
    
    def get_holdings(self, account_id: str) -> List[Holding]:
        """
        Get holdings for a specific account.
        
        Args:
            account_id: Account ID to get holdings for
        
        Returns:
            List of Holding objects
        """
        try:
            response = self.client.account_information.get_user_account_positions(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id
            )
            
            holdings = []
            for position in response.body:
                symbol_data = position.get("symbol", {})
                holding = Holding(
                    symbol=symbol_data.get("symbol", ""),
                    name=symbol_data.get("description", ""),
                    quantity=float(position.get("units", 0)),
                    price=float(position.get("price", 0)),
                    market_value=float(position.get("market_value", 0)),
                    currency=position.get("currency", {}).get("code", "CAD"),
                    percent_of_portfolio=0.0  # Calculate if needed
                )
                holdings.append(holding)
            
            logger.info(f"Retrieved {len(holdings)} holdings for account {account_id}")
            return holdings
        
        except Exception as e:
            logger.error(f"Failed to get holdings: {e}")
            raise SnapTradeClientError(f"Failed to get holdings: {e}")
    
    # ========================================================================
    # Quote Methods
    # ========================================================================
    
    def get_quotes(self, account_id: str, symbols: str) -> List[Dict]:
        """
        Get quotes for symbols.
        
        Args:
            account_id: Account ID
            symbols: Comma-separated symbols (e.g., "AAPL,GOOGL")
        
        Returns:
            List of quote dictionaries
        """
        try:
            response = self.client.trading.get_user_account_quotes(
                user_id=self.user_id,
                user_secret=self.user_secret,
                symbols=symbols,
                account_id=account_id
            )
            
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to get quotes: {e}")
            raise SnapTradeClientError(f"Failed to get quotes: {e}")
    
    # ========================================================================
    # Trade Execution Methods
    # ========================================================================
    
    def check_order_impact(
        self,
        account_id: str,
        action: str,
        order_type: str,
        price: Optional[float],
        stop: Optional[float],
        time_in_force: str,
        units: Optional[float],
        universal_symbol_id: str
    ) -> Dict:
        """
        Check the impact of an order before placing it.
        
        Returns:
            Dict with order impact including tradeId for placing checked order
        """
        try:
            response = self.client.trading.get_order_impact(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id,
                action=action,
                order_type=order_type,
                price=price,
                stop=stop,
                time_in_force=time_in_force,
                units=units,
                universal_symbol_id=universal_symbol_id
            )
            
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to check order impact: {e}")
            raise SnapTradeClientError(f"Failed to check order impact: {e}")
    
    def place_checked_order(self, account_id: str, trade_id: str) -> Dict:
        """
        Place a checked order using tradeId from check_order_impact.
        
        Args:
            account_id: Account ID
            trade_id: Trade ID from check_order_impact response
        
        Returns:
            Order response from broker
        """
        try:
            response = self.client.trading.place_order(
                trade_id=trade_id,
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            logger.info(f"Placed checked order with tradeId: {trade_id}")
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to place checked order: {e}")
            raise SnapTradeClientError(f"Failed to place order: {e}")
    
    def place_force_order(
        self,
        account_id: str,
        action: str,
        order_type: str,
        price: Optional[float],
        stop: Optional[float],
        time_in_force: str,
        units: Optional[float],
        universal_symbol_id: str
    ) -> Dict:
        """
        Place an order without checking (force place).
        
        Use check_order_impact + place_checked_order for safer workflow.
        """
        try:
            response = self.client.trading.place_force_order(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id,
                action=action,
                order_type=order_type,
                price=price,
                stop=stop,
                time_in_force=time_in_force,
                units=units,
                universal_symbol_id=universal_symbol_id
            )
            
            logger.info(f"Placed force order")
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to place force order: {e}")
            raise SnapTradeClientError(f"Failed to place order: {e}")
    
    def get_account_orders(self, account_id: str) -> List[Dict]:
        """Get all orders for an account."""
        try:
            response = self.client.account_information.get_user_account_orders(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id
            )
            
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            raise SnapTradeClientError(f"Failed to get orders: {e}")
    
    # ========================================================================
    # Connection Methods
    # ========================================================================
    
    def list_connections(self) -> List[Dict]:
        """List all brokerage connections for the user."""
        try:
            response = self.client.connections.list_brokerage_authorizations(
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to list connections: {e}")
            raise SnapTradeClientError(f"Failed to list connections: {e}")
    
    def refresh_connection(self, authorization_id: str) -> Dict:
        """Trigger a manual refresh of a brokerage connection."""
        try:
            response = self.client.connections.refresh_brokerage_authorization(
                authorization_id=authorization_id,
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            logger.info(f"Refreshed connection: {authorization_id}")
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to refresh connection: {e}")
            raise SnapTradeClientError(f"Failed to refresh connection: {e}")
    
    def delete_connection(self, authorization_id: str) -> None:
        """Delete a brokerage connection."""
        try:
            self.client.connections.remove_brokerage_authorization(
                authorization_id=authorization_id,
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            logger.info(f"Deleted connection: {authorization_id}")
        
        except Exception as e:
            logger.error(f"Failed to delete connection: {e}")
            raise SnapTradeClientError(f"Failed to delete connection: {e}")


# ============================================================================
# Helper Functions
# ============================================================================

def list_all_brokerages() -> List[Dict]:
    """Get list of all supported brokerages."""
    try:
        snaptrade = get_snaptrade_client()
        response = snaptrade.reference_data.list_all_brokerage_authorization_type()
        return response.body
    except Exception as e:
        logger.error(f"Failed to list brokerages: {e}")
        raise SnapTradeClientError(f"Failed to list brokerages: {e}")


def list_all_currencies() -> List[Dict]:
    """Get list of all supported currencies."""
    try:
        snaptrade = get_snaptrade_client()
        response = snaptrade.reference_data.list_all_currencies()
        return response.body
    except Exception as e:
        logger.error(f"Failed to list currencies: {e}")
        raise SnapTradeClientError(f"Failed to list currencies: {e}")


# ---------------------------------------------------------------------------
# Convenience functions used by API routers
# ---------------------------------------------------------------------------


def list_accounts(user_id: str, user_secret: str) -> List[Dict]:
    client = SnapTradeClient(user_id, user_secret)
    accounts = client.get_accounts()
    return [account.__dict__ for account in accounts]


def get_symbol_quote(
    ticker: str,
    account_id: str,
    user_id: str,
    user_secret: str,
) -> Dict:
    client = SnapTradeClient(user_id, user_secret)
    quotes = client.get_quotes(account_id, ticker)
    if isinstance(quotes, list) and quotes:
        return quotes[0]
    if isinstance(quotes, dict):
        return quotes
    raise SnapTradeClientError("Quote not found")


def place_equity_order(
    account_id: str,
    user_id: str,
    user_secret: str,
    universal_symbol_id: str,
    action: str,
    order_type: str,
    time_in_force: str,
    units: float,
    limit_price: float | None = None,
):
    client = SnapTradeClient(user_id, user_secret)
    return client.place_force_order(
        account_id=account_id,
        action=action,
        order_type=order_type,
        price=limit_price,
        stop=None,
        time_in_force=time_in_force,
        units=units,
        universal_symbol_id=universal_symbol_id,
    )


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    # Register a new user
    user_data = register_snaptrade_user("test_user_123")
    user_id = user_data["userId"]
    user_secret = user_data["userSecret"]
    
    # Generate connection URL
    connection_url = generate_connection_url(
        user_id=user_id,
        user_secret=user_secret,
        connection_type="trade"
    )
    print(f"Connection URL: {connection_url}")
    
    # Initialize client for user
    client = SnapTradeClient(user_id=user_id, user_secret=user_secret)
    
    # Get accounts (after user connects their brokerage)
    # accounts = client.get_accounts()
    # for account in accounts:
    #     print(f"Account: {account.name} - {account.balance} {account.currency}")