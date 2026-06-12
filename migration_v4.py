"""
Migração v4: economy legada -> tabelas normalizadas + seed de mercado.
Faça backup de database/bot.db antes de executar.
"""
import json
import os
import sqlite3

from economy_constants import FISH_DB
from economy_db import V4_TABLES_SQL, seed_market_prices, sync_user_to_economy

DB_PATH = os.path.join(os.getcwd(), "database", "bot.db")


def _add_column_safe(cursor: sqlite3.Cursor, table: str, column_def: str) -> None:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise


def _migrate_user(cursor: sqlite3.Cursor, row: sqlite3.Row) -> None:
    user_id = row["user_id"]
    cursor.execute(
        """
        INSERT INTO users (user_id, user_name, wallet, fish_count, guild_rank, guild_xp, scrap)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            user_name = excluded.user_name,
            wallet = excluded.wallet,
            fish_count = excluded.fish_count,
            guild_rank = excluded.guild_rank,
            guild_xp = excluded.guild_xp,
            scrap = excluded.scrap
        """,
        (
            user_id,
            row["user_name"],
            row["wallet"] or 0,
            row["fish_count"] or 0,
            row["guild_rank"] or "F",
            row["guild_xp"] or 0,
            row["scrap"] or 0,
        ),
    )

    inv = json.loads(row["inventory"]) if row["inventory"] else {}
    cursor.execute("DELETE FROM user_inventory WHERE user_id = ?", (user_id,))
    for item_key, qtd in inv.items():
        if qtd and qtd > 0:
            cursor.execute(
                """
                INSERT INTO user_inventory (user_id, item_key, quantity) VALUES (?, ?, ?)
                """,
                (user_id, item_key, qtd),
            )

    current_rod = row["current_rod"] if row["current_rod"] else "vara_bambu"
    cursor.execute(
        """
        INSERT INTO user_rods (user_id, current_rod) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET current_rod = excluded.current_rod
        """,
        (user_id, current_rod),
    )

    upgrades = json.loads(row["rod_upgrades"]) if row["rod_upgrades"] else {}
    cursor.execute(
        """
        INSERT INTO rod_upgrades (user_id, luck_level, cd_level) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            luck_level = excluded.luck_level,
            cd_level = excluded.cd_level
        """,
        (user_id, upgrades.get("luck", 0), upgrades.get("cd", 0)),
    )

    trap = json.loads(row["afk_trap"]) if row["afk_trap"] else {}
    if trap:
        cursor.execute(
            """
            INSERT INTO user_trap (user_id, trap_type, status, timer_end, durability)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                trap_type = excluded.trap_type,
                status = excluded.status,
                timer_end = excluded.timer_end,
                durability = excluded.durability
            """,
            (
                user_id,
                trap.get("type"),
                trap.get("status"),
                trap.get("timer_end"),
                trap.get("durability"),
            ),
        )
    else:
        cursor.execute("DELETE FROM user_trap WHERE user_id = ?", (user_id,))

    cursor.execute(
        """
        INSERT INTO user_cooldowns (user_id, last_fish, last_daily, last_explore)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            last_fish = excluded.last_fish,
            last_daily = excluded.last_daily,
            last_explore = excluded.last_explore
        """,
        (user_id, row["last_fish"], row["last_daily"], row["last_explore"]),
    )


def migrate_to_normalized(db_path: str | None = None) -> dict:
    path = db_path or DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Banco nao encontrado: {path}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.executescript(V4_TABLES_SQL)
    _add_column_safe(cursor, "user_cooldowns", "daily_streak INTEGER DEFAULT 0")

    migrated = 0
    try:
        old_rows = cursor.execute("SELECT * FROM economy").fetchall()
        for row in old_rows:
            _migrate_user(cursor, row)
            migrated += 1
    except sqlite3.OperationalError:
        old_rows = []

    conn.commit()

    seed_market_prices(conn, FISH_DB)

    for row in old_rows:
        sync_user_to_economy(conn, row["user_id"])

    conn.commit()

    report = {
        "economy_rows": len(old_rows),
        "users_rows": cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "inventory_rows": cursor.execute("SELECT COUNT(*) FROM user_inventory").fetchone()[0],
        "market_prices_rows": cursor.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0],
        "migrated": migrated,
    }
    conn.close()
    return report


def print_report(report: dict) -> None:
    print("=== Migracao v4 ===")
    print(f"  economy (legado):     {report['economy_rows']} jogadores")
    print(f"  users (normalizado):  {report['users_rows']} jogadores")
    print(f"  user_inventory:       {report['inventory_rows']} itens")
    print(f"  market_prices:        {report['market_prices_rows']} peixes")
    print(f"  migrados nesta exec:  {report['migrated']}")
    if report["economy_rows"] == 0:
        print("  nota: economy vazia — jogadores serao criados ao usar /eco pescar")
    print("Migracao concluida.")


if __name__ == "__main__":
    print_report(migrate_to_normalized())
