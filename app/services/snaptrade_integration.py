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
from ..core.logging import safe_log_id, safe_log_email

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
        logger.warning("Could not parse numeric field %s from value", field_name or "")
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
    average_purchase_price: float = 0.0  # Cost basis per unit


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
                logger.warning("SnapTrade userId %s exists; retrying with fallback", safe_log_id(user_id, 'user'))
                result = _attempt(fallback_id)
                return result["userId"], result["userSecret"]
            except Exception as e2:
                logger.error("Fallback SnapTrade registration failed: %s", e2)
                raise SnapTradeClientError(f"User registration failed after fallback: {e2}")
        logger.error(f"Failed to register SnapTrade user {safe_log_id(user_id, 'user')}: {e}")
        raise SnapTradeClientError(f"User registration failed: {e}")


def reset_snaptrade_user_secret(user_id: str, user_secret: str) -> tuple[str, str]:
    """Reset (rotate) a SnapTrade user's secret and return (userId, userSecret).

    This calls SnapTrade's `resetUserSecret` endpoint. The returned secret must be
    persisted to our DB immediately, otherwise future SnapTrade calls will fail.
    """

    try:
        snaptrade = get_snaptrade_client()
        response = snaptrade.authentication.reset_snap_trade_user_secret(
            user_id=user_id,
            user_secret=user_secret,
        )
        body = response.body if hasattr(response, "body") else response
        if not isinstance(body, dict):
            raise SnapTradeClientError("Unexpected response shape from reset user secret")

        new_user_id = body.get("userId") or body.get("user_id") or user_id
        new_secret = body.get("userSecret") or body.get("user_secret")
        if not new_secret:
            raise SnapTradeClientError("No userSecret returned from reset user secret")

        # SnapTrade should keep the same userId; warn (but still return) if it differs.
        if new_user_id and new_user_id != user_id:
            logger.warning(
                "SnapTrade reset returned different userId (expected %s got %s)",
                safe_log_id(user_id, "snaptrade"),
                safe_log_id(str(new_user_id), "snaptrade"),
            )

        return str(new_user_id), str(new_secret)

    except SnapTradeClientError:
        raise
    except Exception as e:
        logger.error(
            "Failed to reset SnapTrade secret for %s: %s",
            safe_log_id(user_id, "snaptrade"),
            e,
        )
        raise SnapTradeClientError(f"Reset user secret failed: {e}")


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
        
        logger.info(f"Generated connection URL for user: {safe_log_id(user_id, 'user')}")
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
            
            logger.info(f"Retrieved {len(accounts)} accounts for user {safe_log_id(self.user_id, 'user')}")
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
            
            logger.info(f"Retrieved balances for user {safe_log_id(self.user_id, 'user')}: total=${total_balance:.2f}")
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
                            'average_purchase_price': holding.average_purchase_price,
                        }
                        all_holdings.append(holding_dict)
                        total_holdings_value += holding.market_value
                    
                    total_cash += account_cash
                    
                except Exception as e:
                    logger.warning(f"Failed to get holdings for account {safe_log_id(account.id, 'account')}: {e}")
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
                # Log raw position keys for debugging
                logger.info(f"Raw position keys: {position.keys() if isinstance(position, dict) else 'not a dict'}")
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
                
                # Parse currency - SnapTrade typically reports crypto in USD
                currency_data = position.get("currency", {})
                if isinstance(currency_data, str):
                    currency = currency_data
                elif isinstance(currency_data, dict):
                    currency = currency_data.get("code", "USD") or currency_data.get("id", "USD")
                else:
                    currency = "USD"  # Default to USD for crypto exchanges
                
                # Get average_purchase_price for cost basis - check multiple possible fields/locations
                avg_price_raw = (
                    position.get("average_purchase_price") or 
                    position.get("average_cost") or
                    position.get("book_value_per_unit") or
                    position.get("avg_cost") or
                    position.get("cost_per_share") or
                    0
                )
                
                # Also check if it's nested inside symbol data
                if not avg_price_raw and isinstance(symbol_data, dict):
                    avg_price_raw = (
                        symbol_data.get("average_purchase_price") or
                        symbol_data.get("average_cost") or
                        0
                    )
                
                avg_price = _to_float(avg_price_raw, "average_purchase_price")
                logger.info(f"Symbol {symbol_str}: avg_price_raw={avg_price_raw}, parsed avg_price={avg_price}")
                
                # Final price - use market price if available, otherwise average
                final_price = price if price > 0 else avg_price
                
                holding = Holding(
                    symbol=symbol_str,
                    name=name_str,
                    quantity=quantity,
                    price=final_price,
                    market_value=market_value if market_value > 0 else (quantity * final_price if final_price > 0 else 0),
                    currency=currency,
                    percent_of_portfolio=0.0,
                    average_purchase_price=avg_price
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
                        logger.debug(f"Found cash balance: ${cash_amount:.2f} {currency_code}")
            
            logger.info(f"Retrieved {len(holdings)} holdings and ${total_cash:.2f} total cash for account {safe_log_id(account_id, 'account')}")
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

    def _resolve_universal_symbol_id(self, account_id: str, ticker: str) -> str:
        """Resolve a SnapTrade universal_symbol_id for a ticker via quotes."""

        quotes = self.get_quotes(account_id, ticker)
        quote = None
        if isinstance(quotes, list) and quotes:
            quote = quotes[0]
        elif isinstance(quotes, dict):
            quote = quotes

        if not isinstance(quote, dict):
            raise SnapTradeClientError("Quote not found")

        universal_symbol_id = (
            quote.get("universal_symbol", {}).get("id")
            or quote.get("universal_symbol_id")
            or quote.get("symbol_id")
        )
        if not universal_symbol_id:
            raise SnapTradeClientError("Universal symbol id not resolved")
        return str(universal_symbol_id)

    def _as_trade_order(self, raw: object, *, symbol: str, side: str, quantity: float, fallback_price: float = 0.0) -> TradeOrder:
        """Convert a SnapTrade order response into a TradeOrder."""

        payload: Dict = raw if isinstance(raw, dict) else {}
        order_id = (
            payload.get("id")
            or payload.get("order_id")
            or payload.get("orderId")
            or uuid.uuid4().hex
        )
        status = (
            payload.get("status")
            or payload.get("state")
            or payload.get("order_status")
            or "SUBMITTED"
        )
        price = _to_float(payload.get("price", fallback_price), "order.price")
        return TradeOrder(
            order_id=str(order_id),
            symbol=symbol,
            quantity=float(quantity),
            price=float(price),
            side=side,
            status=str(status),
            timestamp=datetime.utcnow(),
        )

    def buy(self, account_id: str, ticker: str, units: float) -> TradeOrder:
        """Convenience: place a market BUY using ticker."""
        universal_symbol_id = self._resolve_universal_symbol_id(account_id, ticker)
        raw = self.place_force_order(
            account_id=account_id,
            action="BUY",
            order_type="market",
            price=None,
            stop=None,
            time_in_force="DAY",
            units=units,
            universal_symbol_id=universal_symbol_id,
        )
        return self._as_trade_order(raw, symbol=ticker, side="BUY", quantity=units)

    def sell(self, account_id: str, ticker: str, units: float) -> TradeOrder:
        """Convenience: place a market SELL using ticker."""
        universal_symbol_id = self._resolve_universal_symbol_id(account_id, ticker)
        raw = self.place_force_order(
            account_id=account_id,
            action="SELL",
            order_type="market",
            price=None,
            stop=None,
            time_in_force="DAY",
            units=units,
            universal_symbol_id=universal_symbol_id,
        )
        return self._as_trade_order(raw, symbol=ticker, side="SELL", quantity=units)

    def cancel_order(self, account_id: str, order_id: str) -> bool:
        """Cancel an order.

        Not currently implemented in this integration layer (SDK endpoint varies by broker).
        """
        raise SnapTradeClientError("Order cancellation is not implemented for this SnapTrade integration")
    
    def get_account_orders(self, account_id: str, days: int = 90) -> List[Dict]:
        """Get all orders for an account.
        
        Args:
            account_id: Account ID to get orders for
            days: Number of days in the past to fetch orders (default 90)
        
        Returns:
            List of order dictionaries
        """
        try:
            response = self.client.account_information.get_user_account_orders(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id,
                days=days
            )
            
            return response.body
        
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            raise SnapTradeClientError(f"Failed to get orders: {e}")
    
    def get_all_account_orders(self, days: int = 90) -> List[Dict]:
        """
        Get orders for all user accounts.
        
        Args:
            days: Number of days to look back for orders
        
        Returns:
            Combined list of all orders across all accounts
        """
        all_orders = []
        try:
            accounts = self.get_accounts()
            for account in accounts:
                try:
                    orders = self.get_account_orders(account.id, days=days)
                    if orders:
                        all_orders.extend(orders)
                except Exception as e:
                    logger.warning(f"Failed to get orders for account {account.id}: {e}")
                    continue
            return all_orders
        except Exception as e:
            logger.error(f"Failed to get all account orders: {e}")
            return []
    
    def get_last_orders_by_symbol(self, days: int = 90) -> Dict[str, Dict]:
        """
        Get order data for each symbol across all accounts.
        
        Calculates:
        - Weighted average BUY price (for cost basis calculation)
        - Most recent order time and action (for display)
        
        Args:
            days: Number of days to look back for orders
        
        Returns:
            Dict mapping symbol -> {time_placed, action, status, price, avg_buy_price}
            Where price is the avg buy price (cost basis) and time_placed is from the last order
        """
        symbol_orders: Dict[str, Dict] = {}
        # Track all buy orders per symbol to calculate weighted average
        symbol_buy_orders: Dict[str, list] = {}
        
        try:
            all_orders = self.get_all_account_orders(days=days)
            logger.info(f"Processing {len(all_orders)} orders for cost basis calculation")
            
            for order in all_orders:
                try:
                    # Extract symbol from nested structure
                    symbol_data = order.get('universal_symbol', {}) or order.get('symbol', {})
                    if isinstance(symbol_data, dict):
                        symbol = symbol_data.get('symbol', '') or symbol_data.get('raw_symbol', '')
                    else:
                        symbol = str(symbol_data) if symbol_data else ''
                    
                    if not symbol:
                        continue
                    
                    # Normalize symbol (uppercase, handle exchange suffixes)
                    symbol = symbol.upper()
                    
                    # Extract order details
                    action = order.get('action', order.get('side', '')).upper()
                    if action not in ('BUY', 'SELL'):
                        # Map common variations
                        action_map = {
                            'BUY_TO_OPEN': 'BUY',
                            'BUY_TO_CLOSE': 'BUY',
                            'SELL_TO_OPEN': 'SELL',
                            'SELL_TO_CLOSE': 'SELL',
                        }
                        action = action_map.get(action, action)
                    
                    # Get time placed - try multiple possible field names
                    time_placed = (
                        order.get('time_placed') or 
                        order.get('execution_time') or
                        order.get('filled_time') or
                        order.get('time_in_force_expiry') or
                        order.get('trade_date') or
                        order.get('created_at') or
                        ''
                    )
                    
                    # Parse time if it's a string
                    if isinstance(time_placed, str) and time_placed:
                        try:
                            # Handle ISO format
                            time_placed = datetime.fromisoformat(time_placed.replace('Z', '+00:00'))
                        except ValueError:
                            try:
                                # Try other common formats
                                time_placed = datetime.strptime(time_placed, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                time_placed = None
                    elif not isinstance(time_placed, datetime):
                        time_placed = None
                    
                    status = order.get('status', 'UNKNOWN').upper()
                    
                    # Only track EXECUTED/FILLED orders
                    if status not in ('EXECUTED', 'FILLED', 'COMPLETE', 'COMPLETED'):
                        continue
                    
                    # Get execution price and quantity for cost basis calculation
                    execution_price = _to_float(order.get('execution_price', 0), 'execution_price')
                    filled_quantity = _to_float(
                        order.get('filled_quantity') or order.get('total_quantity') or order.get('units') or 0,
                        'filled_quantity'
                    )
                    
                    logger.info(f"Order: {symbol} {action} qty={filled_quantity} @ {execution_price} on {time_placed}")
                    
                    # Collect BUY orders for weighted average calculation
                    if action == 'BUY' and execution_price > 0 and filled_quantity > 0:
                        if symbol not in symbol_buy_orders:
                            symbol_buy_orders[symbol] = []
                        symbol_buy_orders[symbol].append({
                            'price': execution_price,
                            'quantity': filled_quantity,
                            'time': time_placed
                        })
                    
                    # Track the most recent order for display (time and action)
                    if symbol not in symbol_orders:
                        symbol_orders[symbol] = {
                            'time_placed': time_placed,
                            'action': action,
                            'status': status,
                            'price': execution_price,
                            'avg_buy_price': 0.0,  # Will be calculated below
                        }
                    else:
                        # Compare times, keep the most recent
                        existing_time = symbol_orders[symbol].get('time_placed')
                        if time_placed and (not existing_time or time_placed > existing_time):
                            symbol_orders[symbol].update({
                                'time_placed': time_placed,
                                'action': action,
                                'status': status,
                            })
                
                except Exception as e:
                    logger.warning(f"Failed to process order for symbol lookup: {e}")
                    continue
            
            # Calculate weighted average BUY price for each symbol (this is the cost basis)
            for symbol, buy_orders in symbol_buy_orders.items():
                total_cost = sum(o['price'] * o['quantity'] for o in buy_orders)
                total_qty = sum(o['quantity'] for o in buy_orders)
                
                if total_qty > 0:
                    avg_buy_price = total_cost / total_qty
                    logger.info(f"Calculated avg buy price for {symbol}: {avg_buy_price:.6f} from {len(buy_orders)} orders (total qty: {total_qty})")
                    
                    if symbol in symbol_orders:
                        symbol_orders[symbol]['avg_buy_price'] = avg_buy_price
                        # Use avg buy price as the primary price (cost basis)
                        symbol_orders[symbol]['price'] = avg_buy_price
                    else:
                        # Symbol only has buy orders, create entry
                        # Get earliest buy time for display
                        earliest_time = min((o['time'] for o in buy_orders if o['time']), default=None)
                        symbol_orders[symbol] = {
                            'time_placed': earliest_time,
                            'action': 'BUY',
                            'status': 'FILLED',
                            'price': avg_buy_price,
                            'avg_buy_price': avg_buy_price,
                        }
            
            logger.info(f"Found orders for {len(symbol_orders)} symbols with cost basis calculated")
            return symbol_orders
            
        except Exception as e:
            logger.error(f"Failed to get last orders by symbol: {e}")
            return {}
    
    def get_account_activities(self, account_id: str) -> List[Dict]:
        """
        Get account activities (transactions) for a specific account.
        Used to calculate cost basis from purchase history.
        
        Args:
            account_id: The account ID to fetch activities for
        
        Returns:
            List of activity dictionaries with symbol, price, units, type, trade_date
        """
        try:
            response = self.client.account_information.get_account_activities(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id
            )
            
            activities = response.body if hasattr(response, 'body') else response
            
            # Handle paginated response
            if isinstance(activities, dict) and 'data' in activities:
                activities = activities['data']
            
            logger.info(f"Retrieved {len(activities) if activities else 0} activities for account {account_id}")
            return activities if isinstance(activities, list) else []
        
        except Exception as e:
            logger.error(f"Failed to get account activities for {account_id}: {e}")
            return []  # Return empty list on error, don't fail the whole sync
    
    def get_all_account_activities(self) -> List[Dict]:
        """
        Get activities for all user accounts.
        
        Returns:
            Combined list of all activities across all accounts
        """
        all_activities = []
        try:
            accounts = self.get_accounts()
            for account in accounts:
                activities = self.get_account_activities(account.id)
                all_activities.extend(activities)
            return all_activities
        except Exception as e:
            logger.error(f"Failed to get all account activities: {e}")
            return []
    
    def calculate_cost_basis(self, activities: List[Dict]) -> Dict[str, float]:
        """
        Calculate average cost basis per symbol from activities.
        
        Uses weighted average of all BUY transactions.
        
        Args:
            activities: List of activities from get_account_activities
            
        Returns:
            Dict mapping symbol -> average cost per unit
        """
        symbol_costs: Dict[str, Dict[str, float]] = {}  # symbol -> {total_cost, total_units}
        
        for activity in activities:
            try:
                activity_type = activity.get('type', '').upper()
                
                # Only consider BUY transactions
                if activity_type not in ('BUY', 'BUY_TO_OPEN'):
                    continue
                
                # Extract symbol from nested structure
                symbol_data = activity.get('symbol', {})
                if isinstance(symbol_data, dict):
                    symbol = symbol_data.get('symbol', '') or symbol_data.get('raw_symbol', '')
                    if isinstance(symbol, dict):
                        symbol = symbol.get('symbol', '') or symbol.get('raw_symbol', '')
                else:
                    symbol = str(symbol_data) if symbol_data else ''
                
                if not symbol:
                    continue
                
                # Store full symbol (uppercase) and also base symbol without exchange suffix
                full_symbol = symbol.upper()
                base_symbol = symbol.split('.')[0].upper()
                
                price = _to_float(activity.get('price'), 'price')
                units = _to_float(activity.get('units'), 'units')
                
                if price <= 0 or units <= 0:
                    continue
                
                # Store for both full and base symbol to support matching
                for sym in [full_symbol, base_symbol]:
                    if sym not in symbol_costs:
                        symbol_costs[sym] = {'total_cost': 0.0, 'total_units': 0.0}
                    symbol_costs[sym]['total_cost'] += price * units
                    symbol_costs[sym]['total_units'] += units
                
            except Exception as e:
                logger.warning(f"Failed to process activity for cost basis: {e}")
                continue
        
        # Calculate average cost per unit
        cost_basis = {}
        for symbol, data in symbol_costs.items():
            if data['total_units'] > 0:
                cost_basis[symbol] = data['total_cost'] / data['total_units']
        
        logger.info(f"Calculated cost basis for {len(cost_basis)} symbols")
        return cost_basis
    
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