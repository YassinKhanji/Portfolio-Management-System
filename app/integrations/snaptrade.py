"""
SnapTrade Integration

Handles portfolio data fetching from SnapTrade API.
SnapTrade provides unified access to multiple brokerages:
- Kraken (for crypto trading)
- WealthSimple (for equities/bonds trading)

Documentation: https://docs.snaptrade.com/
"""

import requests
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SnapTradeClient:
    """
    Client for SnapTrade API integration
    
    SnapTrade provides:
    - Account linking with brokerages (Kraken, WealthSimple, etc.)
    - Portfolio holdings and positions
    - Trade execution
    - Historical transactions
    - Account balance tracking
    """
    
    def __init__(self, client_id: str, consumer_key: str):
        """
        Initialize SnapTrade client
        
        Args:
            client_id: SnapTrade Client ID
            consumer_key: SnapTrade Consumer Key
            
        Get credentials from: https://app.snaptrade.com/
        """
        self.client_id = client_id
        self.consumer_key = consumer_key
        self.base_url = "https://api.snaptrade.com/api/v1"
        
    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        user_id: str, 
        user_secret: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Dict:
        """
        Make authenticated request to SnapTrade API
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            params: Query parameters
            data: Request body data
            
        Returns:
            API response as dictionary
        """
        url = f"{self.base_url}{endpoint}"
        
        headers = {
            "Content-Type": "application/json",
            "clientId": self.client_id
        }
        
        # SnapTrade uses query params for user auth
        if params is None:
            params = {}
        params.update({
            "userId": user_id,
            "userSecret": user_secret
        })
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=data,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"SnapTrade API request failed: {str(e)}")
            raise
    
    def get_user_accounts(self, user_id: str, user_secret: str) -> List[Dict]:
        """
        Get all brokerage accounts for a user
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            
        Returns:
            List of account objects with balances and info
        """
        try:
            logger.info(f"Fetching accounts for user: {user_id}")
            
            accounts = self._make_request(
                method="GET",
                endpoint="/accounts",
                user_id=user_id,
                user_secret=user_secret
            )
            
            logger.info(f"Found {len(accounts)} accounts for user {user_id}")
            return accounts
            
        except Exception as e:
            logger.error(f"Failed to fetch user accounts: {str(e)}")
            return []
    
    def get_account_holdings(
        self, 
        user_id: str, 
        user_secret: str,
        account_id: str
    ) -> List[Dict]:
        """
        Get holdings/positions for a specific account
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            account_id: Brokerage account ID
            
        Returns:
            List of position objects with symbols, quantities, values
        """
        try:
            logger.info(f"Fetching holdings for account: {account_id}")
            
            holdings = self._make_request(
                method="GET",
                endpoint=f"/accounts/{account_id}/holdings",
                user_id=user_id,
                user_secret=user_secret
            )
            
            logger.info(f"Found {len(holdings)} holdings in account {account_id}")
            return holdings
            
        except Exception as e:
            logger.error(f"Failed to fetch account holdings: {str(e)}")
            return []
    
    def get_all_holdings(self, user_id: str, user_secret: str) -> List[Dict]:
        """
        Get all holdings across all accounts for a user
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            
        Returns:
            Combined list of all positions from all accounts
        """
        try:
            logger.info(f"Fetching all holdings for user: {user_id}")
            
            # Get all accounts
            accounts = self.get_user_accounts(user_id, user_secret)
            
            # Get holdings for each account
            all_holdings = []
            for account in accounts:
                account_id = account.get('id')
                if account_id:
                    holdings = self.get_account_holdings(user_id, user_secret, account_id)
                    
                    # Add account info to each holding
                    for holding in holdings:
                        holding['account_id'] = account_id
                        holding['account_name'] = account.get('name', 'Unknown')
                        holding['brokerage'] = account.get('brokerage', {}).get('name', 'Unknown')
                    
                    all_holdings.extend(holdings)
            
            logger.info(f"Total holdings across all accounts: {len(all_holdings)}")
            return all_holdings
            
        except Exception as e:
            logger.error(f"Failed to fetch all holdings: {str(e)}")
            return []
    
    def get_account_balances(self, user_id: str, user_secret: str) -> Dict:
        """
        Get account balances for all accounts
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            
        Returns:
            Dictionary with total balance and per-account balances
        """
        try:
            logger.info(f"Fetching balances for user: {user_id}")
            
            accounts = self.get_user_accounts(user_id, user_secret)
            
            balances = {
                'total_value': 0.0,
                'accounts': []
            }
            
            for account in accounts:
                account_balance = {
                    'account_id': account.get('id'),
                    'account_name': account.get('name', 'Unknown'),
                    'brokerage': account.get('brokerage', {}).get('name', 'Unknown'),
                    'balance': account.get('balance', {}).get('total', 0.0),
                    'cash': account.get('balance', {}).get('cash', 0.0),
                    'currency': account.get('balance', {}).get('currency', 'USD')
                }
                
                balances['accounts'].append(account_balance)
                balances['total_value'] += account_balance['balance']
            
            logger.info(f"Total portfolio value: ${balances['total_value']:,.2f}")
            return balances
            
        except Exception as e:
            logger.error(f"Failed to fetch account balances: {str(e)}")
            return {'total_value': 0.0, 'accounts': []}
    
    def get_activities(
        self, 
        user_id: str, 
        user_secret: str,
        account_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict]:
        """
        Get transaction activities (trades, deposits, withdrawals)
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            account_id: Optional account ID to filter
            start_date: Optional start date (YYYY-MM-DD)
            end_date: Optional end date (YYYY-MM-DD)
            
        Returns:
            List of activity/transaction objects
        """
        try:
            logger.info(f"Fetching activities for user: {user_id}")
            
            params = {}
            if start_date:
                params['startDate'] = start_date
            if end_date:
                params['endDate'] = end_date
            
            if account_id:
                endpoint = f"/accounts/{account_id}/activities"
            else:
                endpoint = "/activities"
            
            activities = self._make_request(
                method="GET",
                endpoint=endpoint,
                user_id=user_id,
                user_secret=user_secret,
                params=params
            )
            
            logger.info(f"Found {len(activities)} activities")
            return activities
            
        except Exception as e:
            logger.error(f"Failed to fetch activities: {str(e)}")
            return []
    
    def execute_trade(
        self,
        user_id: str,
        user_secret: str,
        account_id: str,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        quantity: float,
        order_type: str = "Market"
    ) -> Dict:
        """
        Execute a trade order
        
        Args:
            user_id: SnapTrade user ID
            user_secret: SnapTrade user secret
            account_id: Brokerage account ID
            symbol: Trading symbol (e.g., "BTC", "AAPL")
            action: "BUY" or "SELL"
            quantity: Number of units to trade
            order_type: "Market" or "Limit"
            
        Returns:
            Trade execution result
        """
        try:
            logger.info(f"Executing trade: {action} {quantity} {symbol} on account {account_id}")
            
            trade_data = {
                "account_id": account_id,
                "action": action.upper(),
                "universal_symbol_id": symbol,  # SnapTrade uses universal symbol IDs
                "order_type": order_type,
                "quantity": quantity
            }
            
            result = self._make_request(
                method="POST",
                endpoint="/trade/place",
                user_id=user_id,
                user_secret=user_secret,
                data=trade_data
            )
            
            logger.info(f"Trade executed successfully: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            raise


# Initialize SnapTrade client (credentials should come from environment variables)
def get_snaptrade_client() -> SnapTradeClient:
    """
    Get configured SnapTrade client
    
    Set environment variables:
    - SNAPTRADE_CLIENT_ID
    - SNAPTRADE_CONSUMER_KEY
    """
    import os
    
    client_id = os.getenv('SNAPTRADE_CLIENT_ID', 'your-client-id')
    consumer_key = os.getenv('SNAPTRADE_CONSUMER_KEY', 'your-consumer-key')
    
    return SnapTradeClient(client_id, consumer_key)
