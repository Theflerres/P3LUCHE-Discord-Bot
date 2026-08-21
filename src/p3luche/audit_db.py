"""Script de auditoria do banco: procura wallets inválidas e JSON corrompido.

Uso: python audit_db.py

Diagnóstico sob demanda, não biblioteca. Todo o trabalho vive dentro de main()
porque antes este módulo abria a conexão com o banco no nível de módulo — ou
seja, um mero "import audit_db" já conectava no database/bot.db de produção.
"""
import json
import sqlite3

from config import DB_PATH


def main(db_path: str | None = None) -> None:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        print('DB:', path)
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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
