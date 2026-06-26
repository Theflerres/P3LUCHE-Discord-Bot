import sqlite3
import unittest

from economy_db import ensure_user, ensure_v4_tables, get_wallet


class EconomyDbTests(unittest.TestCase):
    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE economy (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                wallet INTEGER DEFAULT 0,
                fish_count INTEGER DEFAULT 0,
                guild_rank TEXT DEFAULT 'F',
                guild_xp INTEGER DEFAULT 0,
                scrap INTEGER DEFAULT 0,
                inventory TEXT DEFAULT '{}',
                current_rod TEXT DEFAULT 'vara_bambu',
                rod_upgrades TEXT DEFAULT '{}',
                afk_trap TEXT DEFAULT '{}',
                last_fish TIMESTAMP,
                last_daily TIMESTAMP,
                last_explore TIMESTAMP,
                baits INTEGER DEFAULT 0
            )
            """
        )
        ensure_v4_tables(conn)
        return conn

    def test_ensure_user_syncs_existing_users_row_from_economy(self):
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (42, "Teste", 250),
        )
        conn.execute(
            "INSERT INTO users (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (42, "Teste", 0),
        )
        conn.commit()

        ensure_user(conn, 42, "Teste")

        self.assertEqual(get_wallet(conn, 42), 250)

    def test_get_wallet_normalizes_string_values(self):
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (43, "Texto", "300"),
        )
        conn.execute(
            "INSERT INTO users (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (43, "Texto", "0"),
        )
        conn.commit()

        ensure_user(conn, 43, "Texto")

        self.assertEqual(get_wallet(conn, 43), 300)
        self.assertIsInstance(get_wallet(conn, 43), int)


if __name__ == "__main__":
    unittest.main()
