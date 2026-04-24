"""Acesso ao SQLite compartilhado com o bot P3LUCHE."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import load_config


def get_connection() -> sqlite3.Connection:
    """Abre conexão com row_factory em dicionário para facilitar serialização."""
    cfg = load_config()
    db_path = Path(cfg.sqlite_path)
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite não encontrado em: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Valida se uma tabela existe antes de consultar."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def get_lore_entries() -> list[dict[str, Any]]:
    """Retorna lore_entries inteiras para indexação e exibição."""
    with get_connection() as conn:
        if not _table_exists(conn, "lore_entries"):
            return []
        rows = conn.execute("SELECT * FROM lore_entries").fetchall()
        return [dict(r) for r in rows]


def query_lore_search(text: str, limit: int = 50) -> list[dict[str, Any]]:
    """Busca textual simples na tabela de lore para aba de navegação."""
    with get_connection() as conn:
        if not _table_exists(conn, "lore_entries"):
            return []
        q = """
        SELECT *
        FROM lore_entries
        WHERE COALESCE(title, '') LIKE ?
           OR COALESCE(content, '') LIKE ?
           OR COALESCE(tags, '') LIKE ?
        ORDER BY id DESC
        LIMIT ?
        """
        rows = conn.execute(q, (f"%{text}%", f"%{text}%", f"%{text}%", limit)).fetchall()
        return [dict(r) for r in rows]


def get_bot_stats() -> dict[str, Any]:
    """Coleta estatísticas gerais conhecidas de tabelas do bot."""
    stats: dict[str, Any] = {"tables_found": []}
    with get_connection() as conn:
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        stats["tables_found"] = tables

        # Mapeamento tolerante para diferentes nomes de tabelas.
        table_candidates = {
            "usuarios": ["users", "usuarios", "members"],
            "warns": ["warns", "warnings"],
            "musicas": ["musicas", "songs", "music"],
            "economia": ["economia", "economy", "balances"],
            "pescas": ["fishing", "pescas", "fish_logs"],
        }

        for key, candidates in table_candidates.items():
            stats[key] = 0
            for table in candidates:
                if table in tables:
                    try:
                        stats[key] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                        break
                    except sqlite3.Error:
                        continue

        if "fishing" in tables:
            try:
                row = conn.execute("SELECT * FROM fishing ORDER BY id DESC LIMIT 1").fetchone()
                stats["ultima_pesca"] = dict(row) if row else None
            except sqlite3.Error:
                stats["ultima_pesca"] = None
        else:
            stats["ultima_pesca"] = None

    return stats
