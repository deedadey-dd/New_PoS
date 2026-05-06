import sqlite3

conn = sqlite3.connect('db.sqlite3')
cursor = conn.cursor()
cursor.execute("SELECT app, name FROM django_migrations WHERE app IN ('accounting', 'core') ORDER BY id DESC LIMIT 20;")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]}")
conn.close()
