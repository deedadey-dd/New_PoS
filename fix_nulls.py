import sqlite3

conn = sqlite3.connect('db.sqlite3')
cursor = conn.cursor()

# Fix Sales NOT NULL constraints added by alpha
try:
    cursor.execute("UPDATE sales_sale SET is_dispatched = 0 WHERE is_dispatched IS NULL;")
    print(f"Fixed {cursor.rowcount} rows where is_dispatched was NULL.")
except sqlite3.OperationalError:
    pass  # Column might not exist or already dropped

conn.commit()
conn.close()
