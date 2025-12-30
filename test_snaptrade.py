from pprint import pprint
from snaptrade_client import SnapTrade

snaptrade = SnapTrade(
    client_id="KHANJI-EPNQC",
    consumer_key="ifZlBfjQogVmxLSascFvOMW1UyWW9wEvmZPgxLy5c1HzcbKYdw"
)

# Get all SnapTrade users
response = snaptrade.authentication.list_snap_trade_users()
users = response.body

print(f"Found {len(users)} users\n")

# Just delete the users (this should cascade delete connections)
for user_id in users:
    try:
        print(f"Deleting user: {user_id}")
        delete_response = snaptrade.authentication.delete_snap_trade_user(
            user_id=user_id
        )
        print(f"✓ Deleted user: {user_id}\n")
    except Exception as e:
        print(f"✗ Error deleting user {user_id}: {e}\n")

print("Deletion complete!")
print("\nNote: If connections still appear in the dashboard,")
print("you may need to delete them manually from the SnapTrade dashboard.")