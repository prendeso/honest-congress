"""Fix party values in database from title-case to uppercase."""
from src.db.database import engine

connection = engine.raw_connection()
cursor = connection.cursor()

# Update all lowercase party values to uppercase
print('Fixing party values...')
cursor.execute("UPDATE members SET party = 'DEMOCRAT' WHERE party = 'Democrat'")
print(f'  Democrat -> DEMOCRAT: {cursor.rowcount} rows')

cursor.execute("UPDATE members SET party = 'REPUBLICAN' WHERE party = 'Republican'")
print(f'  Republican -> REPUBLICAN: {cursor.rowcount} rows')

cursor.execute("UPDATE members SET party = 'INDEPENDENT' WHERE party = 'Independent'")
print(f'  Independent -> INDEPENDENT: {cursor.rowcount} rows')

connection.commit()
connection.close()

print('✅ Fixed all party values in database')

