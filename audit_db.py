import json
import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print('DB:', DB_PATH)
print('users wallet invalid:')
print([dict(r) for r in conn.execute("SELECT user_id, wallet, typeof(wallet) AS t FROM users WHERE wallet IS NULL OR typeof(wallet) != 'integer'")])
print('economy wallet invalid:')
print([dict(r) for r in conn.execute("SELECT user_id, wallet, typeof(wallet) AS t FROM economy WHERE wallet IS NULL OR typeof(wallet) != 'integer'")])

for table, col in [('economy', 'inventory'), ('economy', 'rod_upgrades'), ('economy', 'afk_trap')]:
    bad = []
    for row in conn.execute(f"SELECT user_id, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''").fetchall():
        try:
            json.loads(row[col])
        except Exception:
            bad.append((row['user_id'], row[col]))
        if len(bad) >= 5:
            break
    print(table, col, 'bad_json', bad)
