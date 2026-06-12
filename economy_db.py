"""
Helpers de economia normalizada (v4) com sincronização da tabela economy legada.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

V4_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    user_name TEXT,
    wallet INTEGER DEFAULT 0,
    fish_count INTEGER DEFAULT 0,
    guild_rank TEXT DEFAULT 'F',
    guild_xp INTEGER DEFAULT 0,
    scrap INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(user_id),
    item_key TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    UNIQUE(user_id, item_key)
);
CREATE TABLE IF NOT EXISTS user_rods (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
    current_rod TEXT DEFAULT 'vara_bambu'
);
CREATE TABLE IF NOT EXISTS rod_upgrades (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
    luck_level INTEGER DEFAULT 0,
    cd_level INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_trap (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
    trap_type TEXT,
    status TEXT,
    timer_end TIMESTAMP,
    durability INTEGER
);
CREATE TABLE IF NOT EXISTS user_cooldowns (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
    last_fish TIMESTAMP,
    last_daily TIMESTAMP,
    last_explore TIMESTAMP,
    daily_streak INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS achievements (
    user_id INTEGER,
    achievement_id TEXT,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, achievement_id)
);
CREATE TABLE IF NOT EXISTS tournament_leaderboard (
    user_id INTEGER,
    tournament_id TEXT,
    points INTEGER,
    PRIMARY KEY (user_id, tournament_id)
);
CREATE TABLE IF NOT EXISTS market_prices (
    fish_name TEXT PRIMARY KEY,
    base_price INTEGER,
    current_price INTEGER,
    last_updated TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fish_sales_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fish_name TEXT NOT NULL,
    sale_price INTEGER NOT NULL,
    user_id INTEGER,
    sale_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS auction_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    highest_bid INTEGER DEFAULT 0,
    highest_bidder INTEGER,
    ends_at TIMESTAMP,
    status TEXT DEFAULT 'active'
);
"""


def ensure_v4_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(V4_TABLES_SQL)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "ALTER TABLE user_cooldowns ADD COLUMN daily_streak INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise
    conn.commit()


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def sync_user_from_economy(conn: sqlite3.Connection, user_id: int) -> None:
    """Migra um usuário da tabela economy para as tabelas normalizadas."""
    cursor = conn.cursor()
    if not _table_exists(cursor, "economy"):
        return
    row = cursor.execute("SELECT * FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return

    cursor.execute(
        """
        INSERT OR IGNORE INTO users (user_id, user_name, wallet, fish_count, guild_rank, guild_xp, scrap)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["user_id"],
            row["user_name"],
            row["wallet"],
            row["fish_count"] or 0,
            row["guild_rank"] or "F",
            row["guild_xp"] or 0,
            row["scrap"] or 0,
        ),
    )

    try:
        inv = json.loads(row["inventory"]) if row["inventory"] else {}
    except (json.JSONDecodeError, TypeError):
        inv = {}
    for item_key, qtd in inv.items():
        if qtd > 0:
            cursor.execute(
                """
                INSERT INTO user_inventory (user_id, item_key, quantity) VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = excluded.quantity
                """,
                (user_id, item_key, qtd),
            )

    current_rod = row["current_rod"] if row["current_rod"] else "vara_bambu"
    cursor.execute(
        "INSERT OR IGNORE INTO user_rods (user_id, current_rod) VALUES (?, ?)",
        (user_id, current_rod),
    )

    try:
        upgrades = json.loads(row["rod_upgrades"]) if row["rod_upgrades"] else {}
    except (json.JSONDecodeError, TypeError):
        upgrades = {}
    cursor.execute(
        """
        INSERT OR IGNORE INTO rod_upgrades (user_id, luck_level, cd_level) VALUES (?, ?, ?)
        """,
        (user_id, upgrades.get("luck", 0), upgrades.get("cd", 0)),
    )

    try:
        trap = json.loads(row["afk_trap"]) if row["afk_trap"] else {}
    except (json.JSONDecodeError, TypeError):
        trap = {}
    if trap:
        cursor.execute(
            """
            INSERT OR IGNORE INTO user_trap (user_id, trap_type, status, timer_end, durability)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                trap.get("type"),
                trap.get("status"),
                trap.get("timer_end"),
                trap.get("durability"),
            ),
        )

    cursor.execute(
        """
        INSERT OR IGNORE INTO user_cooldowns (user_id, last_fish, last_daily, last_explore)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, row["last_fish"], row["last_daily"], row["last_explore"]),
    )
    conn.commit()


def sync_user_to_economy(conn: sqlite3.Connection, user_id: int) -> None:
    """Sincroniza tabelas normalizadas de volta para economy (compatibilidade legada)."""
    cursor = conn.cursor()
    if not _table_exists(cursor, "economy"):
        return

    user = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        return

    inv_rows = cursor.execute(
        "SELECT item_key, quantity FROM user_inventory WHERE user_id = ?", (user_id,)
    ).fetchall()
    inv = {r["item_key"]: r["quantity"] for r in inv_rows if r["quantity"] > 0}

    rod = cursor.execute(
        "SELECT current_rod FROM user_rods WHERE user_id = ?", (user_id,)
    ).fetchone()
    current_rod = rod["current_rod"] if rod else "vara_bambu"

    upg = cursor.execute(
        "SELECT luck_level, cd_level FROM rod_upgrades WHERE user_id = ?", (user_id,)
    ).fetchone()
    upgrades = {"luck": upg["luck_level"] if upg else 0, "cd": upg["cd_level"] if upg else 0}

    trap = cursor.execute("SELECT * FROM user_trap WHERE user_id = ?", (user_id,)).fetchone()
    trap_json = {}
    if trap and trap["trap_type"]:
        trap_json = {
            "type": trap["trap_type"],
            "status": trap["status"],
            "timer_end": trap["timer_end"],
            "durability": trap["durability"],
        }

    cd = cursor.execute(
        "SELECT last_fish, last_daily, last_explore FROM user_cooldowns WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    baits = inv.get("isca", 0)
    cursor.execute(
        """
        INSERT INTO economy (user_id, user_name, wallet, fish_count, guild_rank, guild_xp, scrap,
                             inventory, current_rod, rod_upgrades, afk_trap, last_fish, last_daily,
                             last_explore, baits)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            user_name = excluded.user_name,
            wallet = excluded.wallet,
            fish_count = excluded.fish_count,
            guild_rank = excluded.guild_rank,
            guild_xp = excluded.guild_xp,
            scrap = excluded.scrap,
            inventory = excluded.inventory,
            current_rod = excluded.current_rod,
            rod_upgrades = excluded.rod_upgrades,
            afk_trap = excluded.afk_trap,
            last_fish = excluded.last_fish,
            last_daily = excluded.last_daily,
            last_explore = excluded.last_explore,
            baits = excluded.baits
        """,
        (
            user_id,
            user["user_name"],
            user["wallet"],
            user["fish_count"],
            user["guild_rank"],
            user["guild_xp"],
            user["scrap"],
            json.dumps(inv),
            current_rod,
            json.dumps(upgrades),
            json.dumps(trap_json),
            cd["last_fish"] if cd else None,
            cd["last_daily"] if cd else None,
            cd["last_explore"] if cd else None,
            baits,
        ),
    )
    conn.commit()


def ensure_user(conn: sqlite3.Connection, user_id: int, user_name: str = "") -> None:
    cursor = conn.cursor()
    ensure_v4_tables(conn)
    row = cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        sync_user_from_economy(conn, user_id)
        row = cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, user_name) VALUES (?, ?)",
            (user_id, user_name),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO user_rods (user_id) VALUES (?)", (user_id,)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO rod_upgrades (user_id) VALUES (?)", (user_id,)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO user_cooldowns (user_id) VALUES (?)", (user_id,)
        )
        conn.commit()


def get_wallet(conn: sqlite3.Connection, user_id: int) -> int:
    ensure_user(conn, user_id)
    row = conn.execute("SELECT wallet FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["wallet"] if row else 0


def modify_wallet(conn: sqlite3.Connection, user_id: int, delta: int, user_name: str = "") -> int:
    """Altera saldo com transação imediata. Retorna novo saldo."""
    ensure_user(conn, user_id, user_name)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT wallet FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        new_wallet = max(0, (row["wallet"] if row else 0) + delta)
        conn.execute(
            "UPDATE users SET wallet = ? WHERE user_id = ?", (new_wallet, user_id)
        )
        if user_name:
            conn.execute(
                "UPDATE users SET user_name = ? WHERE user_id = ?", (user_name, user_id)
            )
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return new_wallet
    except Exception:
        conn.rollback()
        raise


def get_inventory(conn: sqlite3.Connection, user_id: int) -> dict:
    ensure_user(conn, user_id)
    rows = conn.execute(
        "SELECT item_key, quantity FROM user_inventory WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r["item_key"]: r["quantity"] for r in rows if r["quantity"] > 0}


def set_inventory_item(conn: sqlite3.Connection, user_id: int, item_key: str, quantity: int) -> None:
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if quantity <= 0:
            conn.execute(
                "DELETE FROM user_inventory WHERE user_id = ? AND item_key = ?",
                (user_id, item_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO user_inventory (user_id, item_key, quantity) VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = excluded.quantity
                """,
                (user_id, item_key, quantity),
            )
        sync_user_to_economy(conn, user_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def add_inventory_item(conn: sqlite3.Connection, user_id: int, item_key: str, delta: int) -> int:
    inv = get_inventory(conn, user_id)
    new_qty = inv.get(item_key, 0) + delta
    set_inventory_item(conn, user_id, item_key, new_qty)
    return new_qty


def count_fish_in_inventory(conn: sqlite3.Connection, user_id: int, fish_name: str) -> int:
    inv = get_inventory(conn, user_id)
    return inv.get(fish_name, 0)


def consume_fish(conn: sqlite3.Connection, user_id: int, fish_name: str, amount: int) -> bool:
    current = count_fish_in_inventory(conn, user_id, fish_name)
    if current < amount:
        return False
    set_inventory_item(conn, user_id, fish_name, current - amount)
    return True


def log_fish_sale(conn: sqlite3.Connection, fish_name: str, sale_price: int, user_id: int) -> None:
    conn.execute(
        "INSERT INTO fish_sales_history (fish_name, sale_price, user_id) VALUES (?, ?, ?)",
        (fish_name, sale_price, user_id),
    )
    conn.commit()


def seed_market_prices(conn: sqlite3.Connection, fish_db: list) -> None:
    now = datetime.now().isoformat()
    for entry in fish_db:
        name, v_min, v_max = entry[0], entry[1], entry[2]
        if v_max <= 0:
            continue
        base = (v_min + v_max) // 2
        conn.execute(
            """
            INSERT OR IGNORE INTO market_prices (fish_name, base_price, current_price, last_updated)
            VALUES (?, ?, ?, ?)
            """,
            (name, base, base, now),
        )
    conn.commit()


def get_market_price(conn: sqlite3.Connection, fish_name: str, fallback: int) -> int:
    row = conn.execute(
        "SELECT current_price FROM market_prices WHERE fish_name = ?", (fish_name,)
    ).fetchone()
    return row["current_price"] if row else fallback
