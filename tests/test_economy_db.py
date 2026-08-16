import sqlite3
import unittest

from economy_db import (
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_current_rod,
    get_rod_upgrades,
    get_scrap,
    get_wallet,
    modify_scrap,
    set_cooldown,
    set_current_rod,
    try_spend_wallet,
    try_upgrade_rod,
)


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

    def test_try_spend_wallet_deducts_when_sufficient(self):
        conn = self._make_conn()
        ensure_user(conn, 50, "Rico")
        conn.execute("UPDATE users SET wallet = 1000 WHERE user_id = ?", (50,))
        conn.commit()

        ok = try_spend_wallet(conn, 50, 400)

        self.assertTrue(ok)
        self.assertEqual(get_wallet(conn, 50), 600)

    def test_try_spend_wallet_refuses_without_writing_when_insufficient(self):
        conn = self._make_conn()
        ensure_user(conn, 51, "Pobre")
        conn.execute("UPDATE users SET wallet = 100 WHERE user_id = ?", (51,))
        conn.commit()

        ok = try_spend_wallet(conn, 51, 400)

        self.assertFalse(ok)
        self.assertEqual(get_wallet(conn, 51), 100)

    def test_modify_scrap_floors_at_zero(self):
        conn = self._make_conn()
        ensure_user(conn, 52, "Sucateiro")

        self.assertEqual(modify_scrap(conn, 52, 30), 30)
        self.assertEqual(modify_scrap(conn, 52, -100), 0)
        self.assertEqual(get_scrap(conn, 52), 0)

    def test_current_rod_get_set(self):
        conn = self._make_conn()
        ensure_user(conn, 53, "Pescador")

        self.assertEqual(get_current_rod(conn, 53), "vara_bambu")

        set_current_rod(conn, 53, "vara_iridium")

        self.assertEqual(get_current_rod(conn, 53), "vara_iridium")
        row = conn.execute(
            "SELECT current_rod FROM economy WHERE user_id = ?", (53,)
        ).fetchone()
        self.assertEqual(row["current_rod"], "vara_iridium")

    def test_try_upgrade_rod_recomputes_cost_from_fresh_level(self):
        conn = self._make_conn()
        ensure_user(conn, 54, "Tunador")
        modify_scrap(conn, 54, 1000)

        first = try_upgrade_rod(conn, 54, "luck", cost_per_level=100, max_level=5)
        self.assertTrue(first["success"])
        self.assertEqual(first["cost"], 100)
        self.assertEqual(first["level"], 1)
        self.assertEqual(get_rod_upgrades(conn, 54)["luck"], 1)

        # Custo do 2º nível é recalculado a partir do nível fresco (1), não
        # de um custo capturado antes da 1ª compra — deve ser 200, não 100.
        second = try_upgrade_rod(conn, 54, "luck", cost_per_level=100, max_level=5)
        self.assertTrue(second["success"])
        self.assertEqual(second["cost"], 200)
        self.assertEqual(second["level"], 2)
        self.assertEqual(get_scrap(conn, 54), 1000 - 100 - 200)

    def test_try_upgrade_rod_insufficient_scrap_does_not_change_state(self):
        conn = self._make_conn()
        ensure_user(conn, 55, "SemGrana")
        modify_scrap(conn, 55, 50)

        result = try_upgrade_rod(conn, 55, "cd", cost_per_level=100, max_level=5)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "insufficient_scrap")
        self.assertEqual(get_scrap(conn, 55), 50)
        self.assertEqual(get_rod_upgrades(conn, 55)["cd"], 0)

    def test_try_upgrade_rod_respects_max_level(self):
        conn = self._make_conn()
        ensure_user(conn, 56, "Maxado")
        modify_scrap(conn, 56, 100000)

        for _ in range(5):
            result = try_upgrade_rod(conn, 56, "luck", cost_per_level=100, max_level=5)
            self.assertTrue(result["success"])

        blocked = try_upgrade_rod(conn, 56, "luck", cost_per_level=100, max_level=5)

        self.assertFalse(blocked["success"])
        self.assertEqual(blocked["reason"], "max_level")
        self.assertEqual(get_rod_upgrades(conn, 56)["luck"], 5)

    def test_cooldowns_get_set(self):
        conn = self._make_conn()
        ensure_user(conn, 57, "Cooldownado")

        self.assertIsNone(get_cooldowns(conn, 57)["last_fish"])

        set_cooldown(conn, 57, "last_fish", "2026-08-13 12:00:00.000000")

        self.assertEqual(get_cooldowns(conn, 57)["last_fish"], "2026-08-13 12:00:00.000000")
        row = conn.execute(
            "SELECT last_fish FROM economy WHERE user_id = ?", (57,)
        ).fetchone()
        self.assertEqual(row["last_fish"], "2026-08-13 12:00:00.000000")

    def test_set_cooldown_rejects_unknown_field(self):
        conn = self._make_conn()
        ensure_user(conn, 58, "Invalido")

        with self.assertRaises(ValueError):
            set_cooldown(conn, 58, "daily_streak", 5)


if __name__ == "__main__":
    unittest.main()
