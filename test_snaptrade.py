import os
from snaptrade_client import SnapTrade

# Initialize the SnapTrade client
snaptrade = SnapTrade(
    consumer_key='24zj0K4yL2PT0Gm55vgQqCYsMGIEtc4QVSUtcrRACIL1KJdjc8',
    client_id='CONCORDIA-TEST-YMNQY'
)

response = snaptrade.authentication.register_snap_trade_user(
    user_id="yassintest_user_5"
)

# Generate connection URL using the SDK
response = snaptrade.authentication.login_snap_trade_user(
    user_id=response.body['userId'],
    user_secret=response.body['userSecret'],
    connection_type="trade"
)

print(f"Connection URL: {response.body['redirectURI']}")