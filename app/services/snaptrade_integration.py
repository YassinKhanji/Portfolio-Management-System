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


def _to_float(value, field_name: str = "") -> float:
    """Best-effort numeric coercion for SnapTrade payloads.

    Handles nested dict/list payloads like {"amount": {"value": 10}} and
    stringified numbers without raising.
    """

    if value is None:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value)
        if isinstance(value, dict):
            for key in ("amount", "total", "value", "net", "gross"):
                if key in value:
                    return _to_float(value.get(key), field_name)
        if isinstance(value, list) and value:
            return _to_float(value[0], field_name)
    except (TypeError, ValueError):
        logger.warning("Could not parse numeric field %s from value %r", field_name or "", value)
    return 0.0


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
class HoldingsResult:
    """Result from get_holdings containing positions and cash balances"""
    holdings: List['Holding']
    cash_balances: Dict[str, float]  # currency -> amount
    total_cash: float


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


def register_snaptrade_user(user_id: str) -> tuple[str, str]:
    """
    Register a new SnapTrade user and return (userId, userSecret).

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
        result = _attempt(user_id)
        return result["userId"], result["userSecret"]
    except Exception as e:
        msg = str(e) if e else ""
        # SnapTrade 1010: user already exists
        if "already exist" in msg or "1010" in msg:
            fallback_id = f"{user_id}-{uuid.uuid4().hex[:8]}"
            try:
                logger.warning("SnapTrade userId %s exists; retrying with %s", user_id, fallback_id)
                result = _attempt(fallback_id)
                return result["userId"], result["userSecret"]
            except Exception as e2:
                logger.error("Fallback SnapTrade registration failed for %s: %s", fallback_id, e2)
                raise SnapTradeClientError(f"User registration failed after fallback: {e2}")
        logger.error(f"Failed to register SnapTrade user {user_id}: {e}")
        raise SnapTradeClientError(f"User registration failed: {e}")


def generate_connection_url(
    user_id: str,
    user_secret: str,
    broker: Optional[str] = None,
    custom_redirect: Optional[str] = None,
    immediate_redirect: Optional[bool] = None,
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
        if custom_redirect is not None:
            kwargs["custom_redirect"] = custom_redirect
        elif settings.SNAPTRADE_REDIRECT_URI:
            kwargs["custom_redirect"] = settings.SNAPTRADE_REDIRECT_URI
        if immediate_redirect is not None:
            kwargs["immediate_redirect"] = immediate_redirect
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
    return register_snaptrade_user(user_email)


def build_connect_url(
    user_id: str,
    user_secret: str,
    broker: str | None = None,
    connection_type: str = "trade",
    custom_redirect: str | None = None,
    immediate_redirect: bool = True,
) -> str:
    """Thin wrapper to generate a SnapTrade connect URL."""
    return generate_connection_url(
        user_id=user_id,
        user_secret=user_secret,
        broker=broker,
        custom_redirect=custom_redirect,
        immediate_redirect=immediate_redirect,
        connection_type=connection_type,
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
                balance_block = account_data.get("balance", {}) if isinstance(account_data, dict) else {}
                balance_value = balance_block.get("total") if isinstance(balance_block, dict) else balance_block

                cash_restrictions = account_data.get("cash_restrictions", []) if isinstance(account_data, dict) else []
                buying_power_block = None
                if isinstance(cash_restrictions, list) and cash_restrictions:
                    first_restriction = cash_restrictions[0]
                    if isinstance(first_restriction, dict):
                        buying_power_block = first_restriction.get("buying_power")
                    else:
                        buying_power_block = first_restriction

                account = Account(
                    id=account_data.get("id", ""),
                    name=account_data.get("name", ""),
                    broker=account_data.get("institution_name", ""),
                    currency=account_data.get("currency", "CAD"),
                    type=account_data.get("meta", {}).get("type", ""),
                    balance=_to_float(balance_value, "balance.total"),
                    buying_power=_to_float(buying_power_block, "cash_restrictions.buying_power"),
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
    
    def get_account_balances(self) -> Dict[str, any]:
        """
        Get account balances for all user accounts.
        Uses getUserAccountBalance API endpoint.
        
        Returns:
            Dict with total_balance, accounts list with individual balances
        """
        try:
            response = self.client.account_information.get_user_account_balance(
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            response_data = response.body if hasattr(response, 'body') else response
            
            accounts_balances = []
            total_balance = 0.0
            
            # Handle list of account balances
            balances_list = response_data if isinstance(response_data, list) else [response_data] if response_data else []
            
            for account_data in balances_list:
                if isinstance(account_data, dict):
                    # Extract cash balance
                    cash = _to_float(account_data.get('cash', 0), 'cash')
                    
                    # Extract currency
                    currency_data = account_data.get('currency', {})
                    if isinstance(currency_data, dict):
                        currency = currency_data.get('code', 'CAD')
                    else:
                        currency = str(currency_data) if currency_data else 'CAD'
                    
                    accounts_balances.append({
                        'cash': cash,
                        'currency': currency,
                    })
                    total_balance += cash
                else:
                    # Handle object-style response
                    cash = _to_float(getattr(account_data, 'cash', 0), 'cash')
                    currency = getattr(account_data, 'currency', {})
                    if hasattr(currency, 'code'):
                        currency = currency.code
                    elif isinstance(currency, dict):
                        currency = currency.get('code', 'CAD')
                    else:
                        currency = 'CAD'
                    
                    accounts_balances.append({
                        'cash': cash,
                        'currency': currency,
                    })
                    total_balance += cash
            
            logger.info(f"Retrieved balances for user {self.user_id}: total={total_balance}")
            return {
                'total_balance': total_balance,
                'accounts': accounts_balances,
            }
            
        except Exception as e:
            logger.error(f"Failed to get account balances: {e}")
            raise SnapTradeClientError(f"Failed to get account balances: {e}")
    
    def get_all_holdings_with_balances(self) -> Dict[str, any]:
        """
        Get all holdings and balances across all accounts.
        
        Returns:
            Dict with total_value, holdings list, cash_balances
        """
        try:
            accounts = self.get_accounts()
            
            all_holdings = []
            total_holdings_value = 0.0
            total_cash = 0.0
            cash_balances = {}
            
            for account in accounts:
                try:
                    result = self.get_holdings(account.id)
                    
                    # Handle HoldingsResult object
                    if hasattr(result, 'holdings'):
                        holdings = result.holdings
                        account_cash = result.total_cash
                        for currency, amount in result.cash_balances.items():
                            cash_balances[currency] = cash_balances.get(currency, 0) + amount
                    else:
                        holdings = result if isinstance(result, list) else []
                        account_cash = 0.0
                    
                    for holding in holdings:
                        holding_dict = {
                            'symbol': holding.symbol,
                            'name': holding.name,
                            'quantity': holding.quantity,
                            'price': holding.price,
                            'market_value': holding.market_value,
                            'currency': holding.currency,
                            'account_id': account.id,
                            'account_name': account.name,
                            'broker': account.broker,
                        }
                        all_holdings.append(holding_dict)
                        total_holdings_value += holding.market_value
                    
                    total_cash += account_cash
                    
                except Exception as e:
                    logger.warning(f"Failed to get holdings for account {account.id}: {e}")
                    continue
            
            return {
                'total_value': total_holdings_value + total_cash,
                'holdings_value': total_holdings_value,
                'cash_value': total_cash,
                'cash_balances': cash_balances,
                'holdings': all_holdings,
                'accounts_count': len(accounts),
            }
            
        except Exception as e:
            logger.error(f"Failed to get all holdings: {e}")
            raise SnapTradeClientError(f"Failed to get all holdings: {e}")
    
    # ========================================================================
    # Holdings Methods
    # ========================================================================
    
    def get_holdings(self, account_id: str) -> List[Holding]:
        """
        Get holdings for a specific account using get_user_holdings endpoint.
        This endpoint returns positions with market values.
        
        Args:
            account_id: Account ID to get holdings for
        
        Returns:
            List of Holding objects
        """
        try:
            # Use get_user_holdings which returns richer data including market values
            response = self.client.account_information.get_user_holdings(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id
            )
            
            holdings = []
            response_data = response.body
            
            # get_user_holdings returns different structure - positions may be nested
            positions_list = []
            if isinstance(response_data, dict):
                positions_list = response_data.get("positions", []) or response_data.get("holdings", []) or []
                # Log raw response structure for debugging
                logger.info(f"Holdings response keys: {response_data.keys()}")
            elif isinstance(response_data, list):
                positions_list = response_data
            
            logger.info(f"Found {len(positions_list)} positions in response")
            
            # First pass: collect all prices by base symbol for staking variants
            symbol_prices = {}
            for position in positions_list:
                symbol_data = position.get("symbol", {})
                inner_symbol = symbol_data.get("symbol", {}) if isinstance(symbol_data, dict) else {}
                
                if isinstance(inner_symbol, dict):
                    symbol_str = inner_symbol.get("symbol", "") or inner_symbol.get("raw_symbol", "")
                elif isinstance(inner_symbol, str):
                    symbol_str = inner_symbol
                else:
                    symbol_str = ""
                
                if not isinstance(symbol_str, str):
                    symbol_str = str(symbol_str) if symbol_str else ""
                
                price_raw = position.get("price", 0)
                price = _to_float(price_raw, "price")
                if price > 0 and symbol_str:
                    # Store price by base symbol (strip numbers from end for staking variants)
                    base_symbol = ''.join(c for c in symbol_str if not c.isdigit()).upper()
                    symbol_prices[base_symbol] = price
                    symbol_prices[symbol_str.upper()] = price
            
            for position in positions_list:
                # Log raw position for debugging
                logger.info(f"Raw position data: {position}")
                
                # Handle deeply nested symbol structure from SnapTrade
                # Structure: position['symbol']['symbol'] contains {'symbol': 'ATOM', 'description': '...', ...}
                symbol_data = position.get("symbol", {})
                inner_symbol = symbol_data.get("symbol", {}) if isinstance(symbol_data, dict) else {}
                
                # Extract the actual symbol string - it can be nested multiple levels
                if isinstance(inner_symbol, dict):
                    symbol_str = inner_symbol.get("symbol", "") or inner_symbol.get("raw_symbol", "")
                    name_str = inner_symbol.get("description", "") or inner_symbol.get("name", "") or symbol_str
                elif isinstance(inner_symbol, str):
                    symbol_str = inner_symbol
                    name_str = inner_symbol
                else:
                    symbol_str = str(symbol_data.get("symbol", "")) if isinstance(symbol_data, dict) else str(symbol_data)
                    name_str = symbol_str
                
                # Ensure symbol_str is actually a string
                if not isinstance(symbol_str, str):
                    symbol_str = str(symbol_str) if symbol_str else ""
                if not isinstance(name_str, str):
                    name_str = symbol_str
                
                # Parse quantity - handle nested structures
                units = position.get("units", 0) or position.get("quantity", 0)
                quantity = _to_float(units, "units")
                
                # Parse price - might be nested in different ways
                price_raw = position.get("price", 0)
                price = _to_float(price_raw, "price")
                
                # For staking variants (like KSM07), try to use base token price
                if price == 0:
                    base_symbol = ''.join(c for c in symbol_str if not c.isdigit()).upper()
                    if base_symbol in symbol_prices:
                        price = symbol_prices[base_symbol]
                        logger.info(f"Using base symbol {base_symbol} price {price} for {symbol_str}")
                
                # Parse market_value (different keys possible)
                market_value_raw = (
                    position.get("market_value") or
                    position.get("open_pnl") or  # Some brokers use this
                    position.get("value") or
                    0
                )
                market_value = _to_float(market_value_raw, "market_value")
                
                # If market_value is 0 but we have quantity and price, calculate it
                if market_value == 0 and quantity > 0 and price > 0:
                    market_value = quantity * price
                    logger.info(f"Calculated market_value: {quantity} * {price} = {market_value}")
                
                # Parse currency
                currency_data = position.get("currency", {})
                if isinstance(currency_data, str):
                    currency = currency_data
                elif isinstance(currency_data, dict):
                    currency = currency_data.get("code", "CAD") or currency_data.get("id", "CAD")
                else:
                    currency = "CAD"
                
                # Also check for average_purchase_price for cost basis
                avg_price_raw = position.get("average_purchase_price", 0) or position.get("average_cost", 0)
                avg_price = _to_float(avg_price_raw, "average_purchase_price")
                
                # Final price - use market price if available, otherwise average
                final_price = price if price > 0 else avg_price
                
                holding = Holding(
                    symbol=symbol_str,
                    name=name_str,
                    quantity=quantity,
                    price=final_price,
                    market_value=market_value if market_value > 0 else (quantity * final_price if final_price > 0 else 0),
                    currency=currency,
                    percent_of_portfolio=0.0
                )
                holdings.append(holding)
                
                logger.info(f"Parsed holding: {symbol_str}, qty={quantity}, price={final_price}, avg_price={avg_price}, value={holding.market_value}")
            
            # Extract cash balances from the 'balances' field
            cash_balances: Dict[str, float] = {}
            total_cash = 0.0
            balances_list = response_data.get("balances", []) if isinstance(response_data, dict) else []
            logger.info(f"Raw balances data: {balances_list}")
            
            for balance in balances_list:
                if isinstance(balance, dict):
                    # Get currency code
                    currency_data = balance.get("currency", {})
                    if isinstance(currency_data, dict):
                        currency_code = currency_data.get("code", "USD")
                    else:
                        currency_code = str(currency_data) if currency_data else "USD"
                    
                    # Get cash amount - this is the actual cash, not total account value
                    cash_amount = _to_float(balance.get("cash", 0), "cash")
                    
                    if cash_amount > 0:
                        cash_balances[currency_code] = cash_balances.get(currency_code, 0) + cash_amount
                        total_cash += cash_amount
                        logger.info(f"Found cash balance: {cash_amount} {currency_code}")
            
            logger.info(f"Retrieved {len(holdings)} holdings and {total_cash} total cash for account {account_id}")
            return HoldingsResult(holdings=holdings, cash_balances=cash_balances, total_cash=total_cash)
        
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
    user_id, user_secret = register_snaptrade_user("test_user_123")
    
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