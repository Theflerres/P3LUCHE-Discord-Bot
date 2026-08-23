"""
Helpers de economia normalizada (v4) com sincronização da tabela economy legada.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

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
    daily_streak INTEGER DEFAULT 0,
    last_memoria TIMESTAMP
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
CREATE TABLE IF NOT EXISTS user_islands (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
    tier INTEGER DEFAULT 0,
    layout_json TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_island_structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(user_id),
    structure_key TEXT NOT NULL,
    level INTEGER DEFAULT 0,
    status TEXT DEFAULT 'idle',
    timer_end TIMESTAMP,
    state_json TEXT DEFAULT '{}',
    UNIQUE(user_id, structure_key)
);
CREATE TABLE IF NOT EXISTS user_island_unlocks (
    user_id INTEGER,
    unlock_key TEXT,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, unlock_key)
);
CREATE TABLE IF NOT EXISTS mission_completions (
    leader_id INTEGER,
    mission_id TEXT,
    completed_on DATE,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (leader_id, mission_id, completed_on)
);
"""

# Teto de conclusões de missão por grupo, por dia civil. É o mesmo número de
# missões que o quadro oferece, então o teto é o que a interface já promete.
MISSION_DAILY_CAP = 3


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
    try:
        cursor.execute(
            "ALTER TABLE user_cooldowns ADD COLUMN last_memoria TIMESTAMP"
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
    """Garante as linhas v4 do jogador. NÃO reimporta dados da legada.

    Esta função é chamada no início de praticamente todo helper da v4, ou
    seja, algumas vezes por comando. Enquanto ela chamava
    sync_user_from_economy() incondicionalmente, era a raiz do sync
    assimétrico: aquele sync faz upsert do que a legada TEM mas nunca apaga
    o que sumiu de lá, então qualquer item/valor já removido na v4
    ressuscitava a partir de um JSON legado obsoleto na chamada seguinte.
    A legada é cópia derivada (sync_user_to_economy a reescreve inteira a
    partir da v4), então importar dela de volta a cada chamada invertia a
    fonte de verdade.

    A importação legada->v4 agora acontece só quando o jogador ainda não
    existe em `users` — primeiro contato de uma conta pré-v4. A migração em
    massa continua sendo feita por migration_v4.py, que chama
    sync_user_from_economy() explicitamente.
    """
    cursor = conn.cursor()
    ensure_v4_tables(conn)
    row = cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        # Conta ainda não migrada: puxa o que existir na legada uma vez.
        sync_user_from_economy(conn, user_id)
        row = cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, user_name) VALUES (?, ?)",
            (user_id, user_name),
        )
    # Linhas irmãs são criadas SEMPRE (idempotente, não traz dado da legada):
    # set_current_rod/set_cooldown/try_upgrade_rod fazem UPDATE puro e viram
    # no-op silencioso se a linha não existir. Antes esse era um efeito
    # colateral do sync_user_from_economy que rodava a cada chamada.
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


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def has_account(conn: sqlite3.Connection, user_id: int) -> bool:
    """O jogador já tem conta? Leitura pura — NÃO cria linha nenhuma.

    Deliberadamente não chama ensure_user(): os call sites usam isto como
    portão de "use /eco pescar primeiro", e ensure_user criaria a linha em
    `users`, fazendo o portão passar a aceitar todo mundo.

    Fonte é `users` (v4), não mais `economy`. As duas ficam equivalentes na
    prática: migrate_to_normalized() roda no boot (main.py) e cria uma linha
    em `users` para cada linha da legada.
    """
    ensure_v4_tables(conn)
    row = conn.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None


def get_fish_count(conn: sqlite3.Connection, user_id: int) -> int:
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT fish_count FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return _coerce_int(row["fish_count"] if row else 0)


def get_user_names(conn: sqlite3.Connection, user_ids) -> dict:
    """Nomes de exibição de vários jogadores de uma vez: {user_id: user_name}.

    Leitura pura (sem ensure_user): serve para montar listas de membros de
    grupo, onde criar conta para um id inexistente seria efeito colateral
    indesejado. Ids sem linha simplesmente não aparecem no resultado — mesmo
    comportamento do SELECT ... IN (...) que isto substitui.
    """
    ids = [int(u) for u in user_ids]
    if not ids:
        return {}
    ensure_v4_tables(conn)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT user_id, user_name FROM users WHERE user_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {r["user_id"]: r["user_name"] for r in rows}


def get_top_players(conn: sqlite3.Connection, field: str, limit: int = 10) -> list:
    """Ranking por `field` ('wallet' ou 'fish_count'). Leitura pura.

    Devolve lista de dicts {"user_name": ..., <field>: ...} já ordenada,
    para o call site montar o embed sem tocar em SQL.
    """
    if field not in ("wallet", "fish_count"):
        raise ValueError(f"campo de ranking inválido: {field!r}")
    ensure_v4_tables(conn)
    rows = conn.execute(
        f"SELECT user_name, {field} FROM users ORDER BY {field} DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"user_name": r["user_name"], field: _coerce_int(r[field])} for r in rows]


def get_wallet(conn: sqlite3.Connection, user_id: int) -> int:
    ensure_user(conn, user_id)
    row = conn.execute("SELECT wallet FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _coerce_int(row["wallet"] if row else 0)


def modify_wallet(conn: sqlite3.Connection, user_id: int, delta: int, user_name: str = "") -> int:
    """Altera saldo com transação imediata. Retorna novo saldo."""
    ensure_user(conn, user_id, user_name)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT wallet FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current_wallet = _coerce_int(row["wallet"] if row else 0)
        new_wallet = max(0, current_wallet + delta)
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


def try_spend_wallet(conn: sqlite3.Connection, user_id: int, amount: int, user_name: str = "") -> bool:
    """Deduz `amount` do saldo de forma atômica, só se houver saldo suficiente.

    Substitui o padrão `if wallet < price: recusa` + escrita separada
    (vulnerável a sobrescrever um saldo que mudou entre a checagem e a
    escrita) por uma única transação que relê o saldo na hora de gravar.
    """
    if amount <= 0:
        return True
    ensure_user(conn, user_id, user_name)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT wallet FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current_wallet = _coerce_int(row["wallet"] if row else 0)
        if current_wallet < amount:
            conn.commit()
            return False
        conn.execute(
            "UPDATE users SET wallet = ? WHERE user_id = ?",
            (current_wallet - amount, user_id),
        )
        if user_name:
            conn.execute(
                "UPDATE users SET user_name = ? WHERE user_id = ?", (user_name, user_id)
            )
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def get_scrap(conn: sqlite3.Connection, user_id: int) -> int:
    ensure_user(conn, user_id)
    row = conn.execute("SELECT scrap FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _coerce_int(row["scrap"] if row else 0)


def modify_scrap(conn: sqlite3.Connection, user_id: int, delta: int, user_name: str = "") -> int:
    """Altera sucata com transação imediata. Retorna novo total (piso em 0)."""
    ensure_user(conn, user_id, user_name)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT scrap FROM users WHERE user_id = ?", (user_id,)).fetchone()
        current_scrap = _coerce_int(row["scrap"] if row else 0)
        new_scrap = max(0, current_scrap + delta)
        conn.execute("UPDATE users SET scrap = ? WHERE user_id = ?", (new_scrap, user_id))
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return new_scrap
    except Exception:
        conn.rollback()
        raise


def get_current_rod(conn: sqlite3.Connection, user_id: int) -> str:
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT current_rod FROM user_rods WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["current_rod"] if row and row["current_rod"] else "vara_bambu"


def set_current_rod(conn: sqlite3.Connection, user_id: int, rod_key: str) -> None:
    ensure_user(conn, user_id)
    conn.execute(
        "UPDATE user_rods SET current_rod = ? WHERE user_id = ?", (rod_key, user_id)
    )
    sync_user_to_economy(conn, user_id)
    conn.commit()


def get_rod_upgrades(conn: sqlite3.Connection, user_id: int) -> dict:
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT luck_level, cd_level FROM rod_upgrades WHERE user_id = ?", (user_id,)
    ).fetchone()
    return {
        "luck": _coerce_int(row["luck_level"] if row else 0),
        "cd": _coerce_int(row["cd_level"] if row else 0),
    }


def try_upgrade_rod(
    conn: sqlite3.Connection,
    user_id: int,
    upgrade_type: str,
    cost_per_level: int = 100,
    max_level: int = 5,
) -> dict:
    """Compra atomicamente 1 nível de upgrade de vara (luck ou cd).

    O custo é recalculado a partir do nível ATUAL lido dentro da transação
    (nunca de um valor capturado antes, ex.: na abertura de uma view) —
    mesmo raciocínio do fix de duplicação da pesca.
    """
    if upgrade_type not in ("luck", "cd"):
        raise ValueError(f"upgrade_type inválido: {upgrade_type!r}")
    level_col = f"{upgrade_type}_level"
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        srow = conn.execute(
            "SELECT scrap FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        scrap = _coerce_int(srow["scrap"] if srow else 0)
        urow = conn.execute(
            f"SELECT {level_col} FROM rod_upgrades WHERE user_id = ?", (user_id,)
        ).fetchone()
        current_level = _coerce_int(urow[level_col] if urow else 0)
        cost = (current_level + 1) * cost_per_level

        if current_level >= max_level:
            conn.commit()
            return {"success": False, "reason": "max_level", "scrap": scrap, "level": current_level, "cost": cost}
        if scrap < cost:
            conn.commit()
            return {"success": False, "reason": "insufficient_scrap", "scrap": scrap, "level": current_level, "cost": cost}

        new_scrap = scrap - cost
        new_level = current_level + 1
        conn.execute("UPDATE users SET scrap = ? WHERE user_id = ?", (new_scrap, user_id))
        conn.execute(
            f"UPDATE rod_upgrades SET {level_col} = ? WHERE user_id = ?", (new_level, user_id)
        )
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return {"success": True, "reason": None, "scrap": new_scrap, "level": new_level, "cost": cost}
    except Exception:
        conn.rollback()
        raise


_COOLDOWN_FIELDS = ("last_fish", "last_daily", "last_explore", "last_memoria")


def get_cooldowns(conn: sqlite3.Connection, user_id: int) -> dict:
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT last_fish, last_daily, last_explore, daily_streak, last_memoria FROM user_cooldowns WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return {
        "last_fish": row["last_fish"] if row else None,
        "last_daily": row["last_daily"] if row else None,
        "last_explore": row["last_explore"] if row else None,
        "daily_streak": _coerce_int(row["daily_streak"] if row else 0),
        "last_memoria": row["last_memoria"] if row else None,
    }


def set_cooldown(conn: sqlite3.Connection, user_id: int, field: str, value) -> None:
    if field not in _COOLDOWN_FIELDS:
        raise ValueError(f"campo de cooldown inválido: {field!r}")
    ensure_user(conn, user_id)
    conn.execute(f"UPDATE user_cooldowns SET {field} = ? WHERE user_id = ?", (value, user_id))
    sync_user_to_economy(conn, user_id)
    conn.commit()


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


def get_trap(conn: sqlite3.Connection, user_id: int) -> dict:
    """Estado da armadilha AFK lido da v4 (user_trap).

    Devolve o mesmo formato que a coluna legada `economy.afk_trap` usa
    ({} quando não há armadilha), para os chamadores não precisarem saber de
    onde veio.
    """
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT trap_type, status, timer_end, durability FROM user_trap WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["trap_type"]:
        return {}
    return {
        "type": row["trap_type"],
        "status": row["status"],
        "timer_end": row["timer_end"],
        "durability": row["durability"],
    }


def set_trap(conn: sqlite3.Connection, user_id: int, trap: dict | None) -> None:
    """Grava o estado da armadilha na v4 e propaga para a legada.

    Mesmo motivo do set_inventory_item: gravar direto em `economy.afk_trap`
    não chega na v4 (sync_user_from_economy usa INSERT OR IGNORE e nunca
    atualiza uma linha existente de user_trap), então o estado era revertido
    pelo sync_user_to_economy do comando seguinte — uma armadilha coletada
    voltava para 'ready' e podia ser coletada de novo, indefinidamente.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not trap:
            conn.execute(
                "UPDATE user_trap SET trap_type = NULL, status = NULL, timer_end = NULL, durability = NULL WHERE user_id = ?",
                (user_id,),
            )
        else:
            conn.execute(
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
        sync_user_to_economy(conn, user_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_guild_rank(conn: sqlite3.Connection, user_id: int) -> dict:
    """Rank/XP de guilda lidos da v4 (users).

    A coluna legada `economy.guild_rank`/`guild_xp` é derivada de users por
    sync_user_to_economy, então ela é a cópia — não a fonte.
    """
    ensure_user(conn, user_id)
    row = conn.execute(
        "SELECT guild_rank, guild_xp FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {"rank": "F", "xp": 0}
    return {"rank": row["guild_rank"] or "F", "xp": _coerce_int(row["guild_xp"], 0)}


def set_guild_rank(conn: sqlite3.Connection, user_id: int, rank: str, xp: int) -> None:
    """Grava rank/XP de guilda na v4 e propaga para a legada.

    Mesmo motivo do set_trap: gravar direto em `economy` não chega em users,
    e o sync_user_to_economy do comando seguinte reescreve economy a partir de
    users — uma promoção gravada só na legada era revertida na hora seguinte.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE users SET guild_rank = ?, guild_xp = ? WHERE user_id = ?",
            (rank or "F", max(0, _coerce_int(xp, 0)), user_id),
        )
        sync_user_to_economy(conn, user_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def add_guild_xp(conn: sqlite3.Connection, user_id: int, delta: int) -> int:
    """Soma `delta` ao XP de guilda relendo o valor atual dentro da transação.

    Existe para os caminhos de recompensa de missão em grupo não dependerem de
    um ensure_user() feito por acaso na linha anterior (era o `modify_wallet`
    que os protegia): sem essa releitura, um `UPDATE users SET guild_xp =
    guild_xp + ?` seguido de sync_user_to_economy() propaga para `economy` um
    `users` que pode estar defasado em relação à legada.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT guild_xp FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        new_xp = max(0, _coerce_int(row["guild_xp"] if row else 0) + delta)
        conn.execute(
            "UPDATE users SET guild_xp = ? WHERE user_id = ?", (new_xp, user_id)
        )
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return new_xp
    except Exception:
        conn.rollback()
        raise


# --- TETO DE MISSÕES (por grupo, por dia civil) ---
# A missão era infinitamente repetível: o bloco de conclusão zerava
# active_mission_id/mission_progress e não registrava nada, então bastava
# reaceitar a mesma missão. No rank A isso são 8.000 Sachês por ciclo, sem
# limite. A trava é por leader_id porque a missão vive na tabela `parties`,
# indexada pelo líder — limitação conhecida e documentada: quem troca de grupo
# ao longo do dia contorna o teto.


def _hoje() -> str:
    """Dia civil no mesmo critério do /eco diario (data local, não UTC)."""
    return datetime.now().date().isoformat()


def missions_completed_today(conn: sqlite3.Connection, leader_id: int) -> set:
    ensure_v4_tables(conn)
    rows = conn.execute(
        "SELECT mission_id FROM mission_completions WHERE leader_id = ? AND completed_on = ?",
        (leader_id, _hoje()),
    ).fetchall()
    return {r["mission_id"] for r in rows}


def mission_slots_left(conn: sqlite3.Connection, leader_id: int) -> int:
    return max(0, MISSION_DAILY_CAP - len(missions_completed_today(conn, leader_id)))


def try_register_mission_completion(conn: sqlite3.Connection, leader_id: int, mission_id: str) -> dict:
    """Reserva a conclusão de hoje. Só quem receber success=True pode pagar.

    O INSERT com chave composta é a própria trava: duas conclusões simultâneas
    da mesma missão disputam a mesma linha e só uma insere. Checar antes com um
    SELECT deixaria a janela entre a leitura e a escrita aberta — mesmo
    raciocínio do BEGIN IMMEDIATE no resto da camada.
    """
    ensure_v4_tables(conn)
    hoje = _hoje()
    conn.execute("BEGIN IMMEDIATE")
    try:
        feitas = {
            r["mission_id"]
            for r in conn.execute(
                "SELECT mission_id FROM mission_completions WHERE leader_id = ? AND completed_on = ?",
                (leader_id, hoje),
            ).fetchall()
        }
        if mission_id in feitas:
            conn.commit()
            return {"success": False, "reason": "already_today", "restantes": max(0, MISSION_DAILY_CAP - len(feitas))}
        if len(feitas) >= MISSION_DAILY_CAP:
            conn.commit()
            return {"success": False, "reason": "daily_cap", "restantes": 0}

        cur = conn.execute(
            "INSERT OR IGNORE INTO mission_completions (leader_id, mission_id, completed_on) VALUES (?, ?, ?)",
            (leader_id, mission_id, hoje),
        )
        if cur.rowcount == 0:
            conn.commit()
            return {"success": False, "reason": "already_today", "restantes": max(0, MISSION_DAILY_CAP - len(feitas))}
        conn.commit()
        return {"success": True, "reason": None, "restantes": MISSION_DAILY_CAP - len(feitas) - 1}
    except Exception:
        conn.rollback()
        raise


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


# --- ILHA PESSOAL (Fase 6) ---
# Peixe nunca entra aqui: construção/upgrade só consome Sachê (wallet) e
# sucata (scrap), os mesmos dois recursos já usados em try_upgrade_rod.


def get_island(conn: sqlite3.Connection, user_id: int) -> dict:
    """Retorna a ilha do jogador, criando a linha (tier 0) no primeiro acesso."""
    ensure_user(conn, user_id)
    row = conn.execute("SELECT * FROM user_islands WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO user_islands (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM user_islands WHERE user_id = ?", (user_id,)).fetchone()
    return {
        "user_id": row["user_id"],
        "tier": _coerce_int(row["tier"]),
        "layout_json": row["layout_json"] or "{}",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_island_structures(conn: sqlite3.Connection, user_id: int) -> dict:
    """Retorna {structure_key: {level, status, timer_end, state_json}} das
    construções que já têm alguma linha (iniciadas em algum momento)."""
    ensure_user(conn, user_id)
    rows = conn.execute(
        "SELECT structure_key, level, status, timer_end, state_json FROM user_island_structures WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return {
        r["structure_key"]: {
            "level": _coerce_int(r["level"]),
            "status": r["status"] or "idle",
            "timer_end": r["timer_end"],
            "state_json": r["state_json"] or "{}",
        }
        for r in rows
    }


def get_island_unlocks(conn: sqlite3.Connection, user_id: int) -> set:
    ensure_user(conn, user_id)
    rows = conn.execute(
        "SELECT unlock_key FROM user_island_unlocks WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r["unlock_key"] for r in rows}


def start_island_construction(
    conn: sqlite3.Connection,
    user_id: int,
    structure_key: str,
    target_level: int,
    cost_saches: int,
    cost_scrap: int,
    build_hours: float,
    required_tier: int = 0,
) -> dict:
    """Inicia (ou upa de nível) uma construção da ilha, de forma atômica.

    Relê saldo/sucata, o nível/status ATUAIS da construção e o tier da ilha
    dentro da MESMA transação (nunca um valor capturado na abertura do hub,
    que pode ficar aberto por minutos) — mesmo raciocínio do fix de
    duplicação da pesca e do try_upgrade_rod. O gate de tier
    (`required_tier`) é reavaliado aqui, não só na UI, para não reabrir o
    mesmo tipo de bug de checagem-fora-da-transação corrigido na Fase 3.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        island_row = conn.execute(
            "SELECT tier FROM user_islands WHERE user_id = ?", (user_id,)
        ).fetchone()
        island_tier = _coerce_int(island_row["tier"] if island_row else 0)
        if island_tier < required_tier:
            conn.commit()
            return {"success": False, "reason": "locked", "tier": island_tier}

        srow = conn.execute(
            "SELECT wallet, scrap FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        wallet = _coerce_int(srow["wallet"] if srow else 0)
        scrap = _coerce_int(srow["scrap"] if srow else 0)

        struct_row = conn.execute(
            "SELECT level, status FROM user_island_structures WHERE user_id = ? AND structure_key = ?",
            (user_id, structure_key),
        ).fetchone()
        current_level = _coerce_int(struct_row["level"] if struct_row else 0)
        status = struct_row["status"] if struct_row else "idle"

        if status == "building":
            conn.commit()
            return {"success": False, "reason": "already_building"}
        if status == "ready":
            conn.commit()
            return {"success": False, "reason": "pending_collect"}
        if current_level >= target_level:
            conn.commit()
            return {"success": False, "reason": "already_built", "level": current_level}
        if wallet < cost_saches or scrap < cost_scrap:
            conn.commit()
            return {
                "success": False,
                "reason": "insufficient_resources",
                "wallet": wallet,
                "scrap": scrap,
            }

        new_wallet = wallet - cost_saches
        new_scrap = scrap - cost_scrap
        timer_end = (datetime.now() + timedelta(hours=build_hours)).timestamp()

        conn.execute("UPDATE users SET wallet = ?, scrap = ? WHERE user_id = ?", (new_wallet, new_scrap, user_id))
        conn.execute(
            """
            INSERT INTO user_island_structures (user_id, structure_key, level, status, timer_end, state_json)
            VALUES (?, ?, ?, 'building', ?, '{}')
            ON CONFLICT(user_id, structure_key) DO UPDATE SET
                status = 'building', timer_end = excluded.timer_end
            """,
            (user_id, structure_key, current_level, timer_end),
        )
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return {"success": True, "reason": None, "timer_end": timer_end, "wallet": new_wallet, "scrap": new_scrap}
    except Exception:
        conn.rollback()
        raise


def finalize_island_construction(
    conn: sqlite3.Connection,
    user_id: int,
    structure_key: str,
    target_level: int,
    is_core: bool,
) -> dict:
    """Conclui uma construção cujo timer já passou: sobe o nível para
    `target_level` e, se for a estrutura-núcleo (`is_core`), também sobe o
    tier da ilha e registra o desbloqueio (mesmo padrão de achievements).

    Atômico: relê status/timer dentro da transação antes de confirmar —
    evita finalizar duas vezes se o botão for clicado em duplicidade.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT level, status, timer_end FROM user_island_structures WHERE user_id = ? AND structure_key = ?",
            (user_id, structure_key),
        ).fetchone()
        if not row or row["status"] != "building":
            conn.commit()
            return {"success": False, "reason": "not_building"}

        timer_end = row["timer_end"] or 0
        now_ts = datetime.now().timestamp()
        if now_ts < timer_end:
            conn.commit()
            return {"success": False, "reason": "not_ready", "timer_end": timer_end}

        conn.execute(
            "UPDATE user_island_structures SET level = ?, status = 'idle', timer_end = NULL WHERE user_id = ? AND structure_key = ?",
            (target_level, user_id, structure_key),
        )
        new_tier = None
        if is_core:
            # Upsert, não UPDATE puro: a linha em user_islands só é criada
            # preguiçosamente por get_island() (ex.: na abertura do /ilha).
            # Se finalize for chamado sem essa linha existir ainda, um
            # UPDATE simples afetaria 0 linhas e o tier nunca subiria.
            conn.execute(
                """
                INSERT INTO user_islands (user_id, tier, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    tier = excluded.tier,
                    updated_at = excluded.updated_at
                """,
                (user_id, target_level),
            )
            conn.execute(
                "INSERT OR IGNORE INTO user_island_unlocks (user_id, unlock_key) VALUES (?, ?)",
                (user_id, f"tier_{target_level}"),
            )
            new_tier = target_level
        conn.commit()
        return {"success": True, "reason": None, "level": target_level, "tier": new_tier}
    except Exception:
        conn.rollback()
        raise


# --- RESET DE PROGRESSO (Fase 7 — /admin economia resetar e /admin sistema resetar_tudo) ---
# Escopo decidido com o dono do bot: inclui saldo, sucata, inventário, vara
# equipada + upgrades, armadilha AFK, cooldowns, rank/XP de guilda,
# quest_progress, a ilha pessoal (Fase 6, construções incluídas) e a posição
# no leaderboard de torneio (pontos vêm de atividade econômica, tratados
# como parte do progresso, não como registro permanente).
# Deliberadamente NÃO inclui: achievements (registro permanente de
# conquistas — tratado como histórico independente do progresso econômico,
# mesmo padrão de conquistas Steam/console) e fish_sales_history (ledger de
# vendas — não é estado do jogador). No reset GLOBAL, achievements TAMBÉM é
# esvaziado (ver reset_all_players), porque ali a operação é "começar do
# zero" para o servidor inteiro, não só zerar a economia de um jogador.


def reset_player_progress(conn: sqlite3.Connection, user_id: int) -> dict:
    """Reset individual e destrutivo de UM jogador. Mantém a linha em
    `users` (não deleta a conta) — zera os campos de progresso e apaga as
    linhas nas tabelas de coleção (inventário, estruturas/desbloqueios da
    ilha). Atômico (BEGIN IMMEDIATE): tudo-ou-nada.
    """
    ensure_user(conn, user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE users SET wallet = 0, fish_count = 0, guild_rank = 'F', guild_xp = 0, scrap = 0 WHERE user_id = ?",
            (user_id,),
        )
        conn.execute("DELETE FROM user_inventory WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE user_rods SET current_rod = 'vara_bambu' WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE rod_upgrades SET luck_level = 0, cd_level = 0 WHERE user_id = ?", (user_id,))
        conn.execute(
            "UPDATE user_trap SET trap_type = NULL, status = NULL, timer_end = NULL, durability = NULL WHERE user_id = ?",
            (user_id,),
        )
        conn.execute(
            """
            UPDATE user_cooldowns SET last_fish = NULL, last_daily = NULL, last_explore = NULL,
                daily_streak = 0, last_memoria = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )
        conn.execute(
            """
            UPDATE quest_progress SET current_chapter = 'inicio', quest_status = 'locked',
                inventory = '{}', reputation = 0
            WHERE user_id = ?
            """,
            (user_id,),
        )
        conn.execute("UPDATE user_islands SET tier = 0, layout_json = '{}' WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_island_structures WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_island_unlocks WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE persistent_catches SET catch_count = 0 WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM tournament_leaderboard WHERE user_id = ?", (user_id,))
        sync_user_to_economy(conn, user_id)
        conn.commit()
        return {"success": True}
    except Exception:
        conn.rollback()
        raise


def reset_all_players(conn: sqlite3.Connection) -> dict:
    """Reset GLOBAL e destrutivo de TODOS os jogadores (`/admin sistema
    resetar_tudo`). Mantém as linhas nas tabelas singleton por jogador
    (users, economy, user_rods, rod_upgrades, user_trap, user_cooldowns,
    quest_progress, user_islands, persistent_catches) — zeradas via UPDATE
    em massa, sem WHERE. Esvazia por completo as tabelas de coleção
    (user_inventory, achievements, tournament_leaderboard,
    user_island_structures, user_island_unlocks) e o registro de grupos
    (parties), que ficariam órfãos/quebrados sem sentido após o reset.

    Deliberadamente NÃO toca: fish_sales_history (ledger histórico),
    market_prices/world_state (estado de mundo, não de jogador) e
    auction_lots (leilão ativo, se houver, fica com estado inconsistente —
    não rode este comando durante um leilão em andamento).

    Atômico (BEGIN IMMEDIATE): tudo-ou-nada. Retorna quantos jogadores
    foram afetados, para o log de auditoria.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        players_row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
        players_affected = players_row["c"] if players_row else 0

        conn.execute(
            "UPDATE users SET wallet = 0, fish_count = 0, guild_rank = 'F', guild_xp = 0, scrap = 0"
        )
        conn.execute(
            """
            UPDATE economy SET wallet = 0, fish_count = 0, guild_rank = 'F', guild_xp = 0, scrap = 0,
                inventory = '{}', current_rod = 'vara_bambu', rod_upgrades = '{}', afk_trap = '{}',
                last_fish = NULL, last_daily = NULL, last_explore = NULL, baits = 0
            """
        )
        conn.execute("UPDATE user_rods SET current_rod = 'vara_bambu'")
        conn.execute("UPDATE rod_upgrades SET luck_level = 0, cd_level = 0")
        conn.execute("UPDATE user_trap SET trap_type = NULL, status = NULL, timer_end = NULL, durability = NULL")
        conn.execute(
            """
            UPDATE user_cooldowns SET last_fish = NULL, last_daily = NULL, last_explore = NULL,
                daily_streak = 0, last_memoria = NULL
            """
        )
        conn.execute(
            """
            UPDATE quest_progress SET current_chapter = 'inicio', quest_status = 'locked',
                inventory = '{}', reputation = 0
            """
        )
        conn.execute("UPDATE user_islands SET tier = 0, layout_json = '{}'")
        conn.execute("UPDATE persistent_catches SET catch_count = 0")

        conn.execute("DELETE FROM user_inventory")
        conn.execute("DELETE FROM achievements")
        conn.execute("DELETE FROM tournament_leaderboard")
        conn.execute("DELETE FROM user_island_structures")
        conn.execute("DELETE FROM user_island_unlocks")
        conn.execute("DELETE FROM parties")

        conn.commit()
        return {"success": True, "players_affected": players_affected}
    except Exception:
        conn.rollback()
        raise
