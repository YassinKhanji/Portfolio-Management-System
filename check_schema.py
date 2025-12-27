import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv('DATABASE_URL')

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# Get positions table columns
print('=== POSITIONS TABLE COLUMNS ===')
cursor.execute("""
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'positions' 
ORDER BY ordinal_position
""")
for row in cursor.fetchall():
    print(f'{row[0]}: {row[1]}')

# Get transactions table columns
print('\n=== TRANSACTIONS TABLE COLUMNS ===')
cursor.execute("""
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'transactions' 
ORDER BY ordinal_position
""")
for row in cursor.fetchall():
    print(f'{row[0]}: {row[1]}')

cursor.close()
conn.close()
