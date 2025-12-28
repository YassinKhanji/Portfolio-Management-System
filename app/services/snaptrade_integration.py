"""
SnapTrade Integration Layer

Abstraction over SnapTrade API for:
- Account and holdings management
- Order execution
- Performance tracking
- OAuth flow

SnapTrade handles Canadian investment accounts with multi-broker support.
"""

import requests
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urlencode
import json
import os
import uuid

logger = logging.getLogger(__name__)

# SnapTrade API endpoints (replace with your URL)
SNAPTRADE_API_URL = os.getenv("SNAPTRADE_API_URL", "https://api.snaptrade.com").rstrip("/")
SNAPTRADE_API_KEY = "YOUR_API_KEY"  # Load from environment in production
SNAPTRADE_CLIENT_ID = os.getenv("SNAPTRADE_CLIENT_ID", "")
SNAPTRADE_CLIENT_SECRET = os.getenv("SNAPTRADE_CLIENT_SECRET", "")
SNAPTRADE_REDIRECT_URI = os.getenv("SNAPTRADE_REDIRECT_URI", "")
SNAPTRADE_SANDBOX = os.getenv("SNAPTRADE_SANDBOX", "True").lower() == "true"
SNAPTRADE_APP_URL = os.getenv("SNAPTRADE_APP_URL", "https://app.snaptrade.com").rstrip("/")


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


class SnapTradeClientError(Exception):
    """SnapTrade API error"""
    pass


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "clientId": SNAPTRADE_CLIENT_ID,
        "consumerKey": SNAPTRADE_CLIENT_SECRET,
    }


def list_accounts(user_id: str, user_secret: str) -> List[Dict]:
    """List connected accounts for a SnapTrade user."""
    url = f"{SNAPTRADE_API_URL}/api/v1/accounts"
    params = {
        "userId": user_id,
        "userSecret": user_secret,
    }
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json() if resp.content else []
    except Exception as exc:  # noqa: BLE001
        logger.error("SnapTrade list_accounts failed: %s", exc)
        raise SnapTradeClientError(f"list_accounts failed: {exc}")


def get_symbol_quote(ticker: str, account_id: str, user_id: str, user_secret: str) -> Dict:
    """Fetch quote and universal symbol ID for a ticker on a given account."""
    url = f"{SNAPTRADE_API_URL}/api/v1/quotes"
    params = {
        "symbols": ticker,
        "accountId": account_id,
        "userId": user_id,
        "userSecret": user_secret,
    }
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        # Expecting data structure with items list; pick first match
        if isinstance(data, dict) and data.get("quotes"):
            quotes = data["quotes"]
        else:
            quotes = data if isinstance(data, list) else []
        if not quotes:
            raise SnapTradeClientError("No quotes returned")
        return quotes[0]
    except SnapTradeClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("SnapTrade get_symbol_quote failed: %s", exc)
        raise SnapTradeClientError(f"quote failed: {exc}")


def place_equity_order(
    *,
    account_id: str,
    user_id: str,
    user_secret: str,
    universal_symbol_id: str,
    action: str,
    order_type: str,
    time_in_force: str,
    units: float,
    limit_price: Optional[float] = None,
) -> Dict:
    """Place an order via SnapTrade."""
    url = f"{SNAPTRADE_API_URL}/api/v1/accounts/{account_id}/orders"
    params = {
        "userId": user_id,
        "userSecret": user_secret,
    }
    payload = {
        "universal_symbol_id": universal_symbol_id,
        "action": action.upper(),
        "order_type": order_type.lower(),
        "time_in_force": time_in_force.upper(),
        "units": units,
    }
    if limit_price is not None:
        payload["limit_price"] = limit_price

    try:
        resp = requests.post(url, headers=_headers(), params=params, json=payload, timeout=20)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        logger.error("SnapTrade place_order failed: %s", exc)
        raise SnapTradeClientError(f"order failed: {exc}")


def provision_snaptrade_user(user_email: str) -> Tuple[str, str]:
    """Create a SnapTrade user/secret via API (registerUser).

    Returns (user_id, user_secret). Falls back to UUIDs if credentials are missing or the API fails.
    """
    if not (SNAPTRADE_CLIENT_ID and SNAPTRADE_CLIENT_SECRET):
        logger.warning("SnapTrade credentials missing; using UUID fallback for %s", user_email)
        return str(uuid.uuid4()), str(uuid.uuid4())

    user_id = str(uuid.uuid4())
    user_secret = str(uuid.uuid4())

    url = f"{SNAPTRADE_API_URL}/api/v1/snapTrade/registerUser"
    payload = {
        "userId": user_id,
        "userSecret": user_secret,
        "redirectURI": SNAPTRADE_REDIRECT_URI or None,
        "sandbox": SNAPTRADE_SANDBOX,
        "metadata": {"email": user_email},
    }
    headers = {
        "Content-Type": "application/json",
        "clientId": SNAPTRADE_CLIENT_ID,
        "consumerKey": SNAPTRADE_CLIENT_SECRET,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return data.get("userId", user_id), data.get("userSecret", user_secret)
    except Exception as exc:  # noqa: BLE001
        logger.error("SnapTrade provisioning failed for %s: %s", user_email, exc)
        return user_id, user_secret


def build_connect_url(
    *,
    user_id: str,
    user_secret: str,
    broker: str,
    connection_type: str = "trade",
) -> str:
    """Build SnapTrade connection portal URL (trade-only)."""

    params = {
        "clientId": SNAPTRADE_CLIENT_ID,
        "userId": user_id,
        "userSecret": user_secret,
        "connectionType": connection_type,
    }
    if SNAPTRADE_REDIRECT_URI:
        redirect_with_broker = SNAPTRADE_REDIRECT_URI
        separator = "&" if "?" in redirect_with_broker else "?"
        redirect_with_broker = f"{redirect_with_broker}{separator}broker={broker}"
        params["redirectURI"] = redirect_with_broker
    if SNAPTRADE_SANDBOX:
        params["sandbox"] = "true"

    query = urlencode(params)
    return f"{SNAPTRADE_APP_URL}/login?{query}"


class SnapTradeClient:
    """
    SnapTrade API client for account and trade management
    """
    
    def __init__(self, user_token: str, api_key: str = SNAPTRADE_API_KEY):
        """
        Initialize SnapTrade client
        
        Args:
            user_token: User's SnapTrade access token (stored in User.snaptrade_token)
            api_key: SnapTrade API key (from environment)
        """
        self.user_token = user_token
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
    
    # ========================================================================
    # Account Methods
    # ========================================================================
    
    def get_accounts(self) -> List[Account]:
        """
        Get all user accounts
        
        Returns:
            List of Account objects
        """
        try:
            url = f"{SNAPTRADE_API_URL}/accounts"
            params = {"userToken": self.user_token}
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            accounts = []
            for account_data in response.json():
                account = Account(
                    id=account_data.get("id"),
                    name=account_data.get("name"),
                    broker=account_data.get("broker"),
                    currency=account_data.get("currency", "CAD"),
                    type=account_data.get("account_type", ""),
                    balance=float(account_data.get("balance", 0)),
                    buying_power=float(account_data.get("buying_power", 0))
                )
                accounts.append(account)
            
            logger.info(f"Retrieved {len(accounts)} accounts for user")
            return accounts
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get accounts: {str(e)}")
            raise SnapTradeClientError(f"Failed to get accounts: {str(e)}")
    
    def get_account(self, account_id: str) -> Optional[Account]:
        """Get single account details"""
        accounts = self.get_accounts()
        for account in accounts:
            if account.id == account_id:
                return account
        return None
    
    # ========================================================================
    # Holdings Methods
    # ========================================================================
    
    def get_holdings(self, account_id: Optional[str] = None) -> List[Holding]:
        """
        Get all holdings across accounts (or specific account)
        
        Args:
            account_id: Optional account ID to filter by
        
        Returns:
            List of Holding objects
        """
        try:
            url = f"{SNAPTRADE_API_URL}/holdings"
            params = {"userToken": self.user_token}
            if account_id:
                params["accountId"] = account_id
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            holdings = []
            for holding_data in response.json():
                holding = Holding(
                    symbol=holding_data.get("symbol"),
                    name=holding_data.get("name", ""),
                    quantity=float(holding_data.get("quantity", 0)),
                    price=float(holding_data.get("price", 0)),
                    market_value=float(holding_data.get("market_value", 0)),
                    currency=holding_data.get("currency", "CAD"),
                    percent_of_portfolio=float(holding_data.get("percent_of_portfolio", 0))
                )
                holdings.append(holding)
            
            logger.info(f"Retrieved {len(holdings)} holdings")
            return holdings
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get holdings: {str(e)}")
            raise SnapTradeClientError(f"Failed to get holdings: {str(e)}")
    
    def get_holding_performance(self, symbol: str, account_id: Optional[str] = None) -> Dict:
        """
        Get performance metrics for a specific holding
        
        Returns:
            Dict with gain, gain_percent, cost_basis, etc.
        """
        try:
            url = f"{SNAPTRADE_API_URL}/holdings/{symbol}/performance"
            params = {"userToken": self.user_token}
            if account_id:
                params["accountId"] = account_id
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            return response.json()
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get performance for {symbol}: {str(e)}")
            raise SnapTradeClientError(f"Failed to get performance: {str(e)}")
    
    # ========================================================================
    # Trade Execution Methods
    # ========================================================================
    
    def place_order(
        self,
        account_id: str,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None
    ) -> TradeOrder:
        """
        Place a trade order
        
        Args:
            account_id: Account to trade in
            symbol: Security symbol (e.g., "TSX:XUS")
            quantity: Number of shares
            side: "BUY" or "SELL"
            order_type: "market" or "limit"
            limit_price: Price for limit orders
        
        Returns:
            TradeOrder with execution details
        """
        try:
            url = f"{SNAPTRADE_API_URL}/accounts/{account_id}/orders"
            
            payload = {
                "userToken": self.user_token,
                "symbol": symbol,
                "quantity": quantity,
                "side": side.upper(),
                "orderType": order_type,
            }
            
            if order_type == "limit" and limit_price:
                payload["limitPrice"] = limit_price
            
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            
            order_data = response.json()
            
            trade = TradeOrder(
                order_id=order_data.get("orderId"),
                symbol=symbol,
                quantity=quantity,
                price=float(order_data.get("executionPrice", limit_price or 0)),
                side=side.upper(),
                status=order_data.get("status", "PENDING"),
                timestamp=datetime.utcnow()
            )
            
            logger.info(f"Order placed: {side} {quantity} {symbol} @ {trade.price}")
            return trade
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to place order: {str(e)}")
            raise SnapTradeClientError(f"Failed to place order: {str(e)}")
    
    def buy(
        self,
        account_id: str,
        symbol: str,
        quantity: float,
        limit_price: Optional[float] = None
    ) -> TradeOrder:
        """Buy shares"""
        return self.place_order(
            account_id=account_id,
            symbol=symbol,
            quantity=quantity,
            side="BUY",
            order_type="limit" if limit_price else "market",
            limit_price=limit_price
        )
    
    def sell(
        self,
        account_id: str,
        symbol: str,
        quantity: float,
        limit_price: Optional[float] = None
    ) -> TradeOrder:
        """Sell shares"""
        return self.place_order(
            account_id=account_id,
            symbol=symbol,
            quantity=quantity,
            side="SELL",
            order_type="limit" if limit_price else "market",
            limit_price=limit_price
        )
    
    def cancel_order(self, account_id: str, order_id: str) -> bool:
        """Cancel pending order"""
        try:
            url = f"{SNAPTRADE_API_URL}/accounts/{account_id}/orders/{order_id}"
            
            response = self.session.delete(
                url,
                params={"userToken": self.user_token}
            )
            response.raise_for_status()
            
            logger.info(f"Order {order_id} cancelled")
            return True
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to cancel order: {str(e)}")
            raise SnapTradeClientError(f"Failed to cancel order: {str(e)}")
    
    # ========================================================================
    # Performance Methods
    # ========================================================================
    
    def get_portfolio_performance(
        self,
        account_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict:
        """
        Get portfolio performance metrics
        
        Args:
            account_id: Optional account filter
            start_date: YYYY-MM-DD format
            end_date: YYYY-MM-DD format
        
        Returns:
            Dict with return_pct, total_gain, total_gain_pct, etc.
        """
        try:
            url = f"{SNAPTRADE_API_URL}/performance"
            
            params = {"userToken": self.user_token}
            if account_id:
                params["accountId"] = account_id
            if start_date:
                params["startDate"] = start_date
            if end_date:
                params["endDate"] = end_date
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            return response.json()
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get performance: {str(e)}")
            raise SnapTradeClientError(f"Failed to get performance: {str(e)}")
    
    # ========================================================================
    # OAuth Methods (for initial authentication)
    # ========================================================================
    
    @staticmethod
    def get_oauth_url(client_id: str, redirect_uri: str) -> str:
        """
        Get OAuth authorization URL for user login
        
        Args:
            client_id: SnapTrade application ID
            redirect_uri: Callback URL after login
        
        Returns:
            Authorization URL for redirect
        """
        url = f"{SNAPTRADE_API_URL}/oauth/authorize"
        params = {
            "clientId": client_id,
            "redirectUri": redirect_uri,
            "scope": "accounts holdings orders"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{url}?{query_string}"
    
    @staticmethod
    def exchange_code_for_token(
        client_id: str,
        client_secret: str,
        authorization_code: str,
        redirect_uri: str
    ) -> str:
        """
        Exchange authorization code for access token
        
        Args:
            client_id: SnapTrade application ID
            client_secret: SnapTrade application secret
            authorization_code: Code from OAuth callback
            redirect_uri: Original callback URL
        
        Returns:
            User access token
        """
        try:
            url = f"{SNAPTRADE_API_URL}/oauth/token"
            
            payload = {
                "clientId": client_id,
                "clientSecret": client_secret,
                "authorizationCode": authorization_code,
                "redirectUri": redirect_uri
            }
            
            response = requests.post(url, json=payload)
            response.raise_for_status()
            
            token = response.json().get("accessToken")
            logger.info("OAuth token exchanged successfully")
            return token
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to exchange OAuth code: {str(e)}")
            raise SnapTradeClientError(f"OAuth exchange failed: {str(e)}")


# ============================================================================
# Token Management (Encryption at rest)
# ============================================================================

class TokenManager:
    """
    Secure token storage and retrieval
    
    In production:
    - Store encrypted tokens in database
    - Use KMS for key management
    - Rotate keys periodically
    """
    
    @staticmethod
    def encrypt_token(token: str, key: Optional[str] = None) -> str:
        """
        Encrypt token for storage
        
        Args:
            token: Plain text token
            key: Encryption key (from environment)
        
        Returns:
            Encrypted token
        """
        # In production, use cryptography library
        # from cryptography.fernet import Fernet
        # cipher = Fernet(key)
        # return cipher.encrypt(token.encode()).decode()
        
        # For now, return as-is (implement real encryption in production)
        return token
    
    @staticmethod
    def decrypt_token(encrypted_token: str, key: Optional[str] = None) -> str:
        """
        Decrypt token from storage
        
        Returns:
            Plain text token
        """
        # In production:
        # cipher = Fernet(key)
        # return cipher.decrypt(encrypted_token.encode()).decode()
        
        return encrypted_token


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    # Initialize client with user token
    client = SnapTradeClient(user_token="user_token_here")
    
    # Get accounts
    accounts = client.get_accounts()
    for account in accounts:
        print(f"Account: {account.name} ({account.broker}) - {account.balance:.2f} {account.currency}")
    
    # Get holdings
    holdings = client.get_holdings()
    for holding in holdings:
        print(f"{holding.symbol}: {holding.quantity} @ {holding.price} = {holding.market_value}")
    
    # Get performance
    perf = client.get_portfolio_performance()
    print(f"Portfolio Return: {perf.get('return_pct', 0):.2%}")
