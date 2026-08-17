import sqlite3
import unittest
from datetime import datetime, timedelta

from economy_db import (
    ensure_v4_tables,
    finalize_island_construction,
    get_island,
    get_island_structures,
    get_island_unlocks,
    get_scrap,
    get_wallet,
    modify_scrap,
    modify_wallet,
    start_island_construction,
)
from cogs.ilha import ISLAND_STRUCTURES, _structure_cost


def _make_conn():
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


def _force_ready(conn, user_id, structure_key):
    """Simula o tempo de construção já ter passado (sem esperar de verdade)."""
    past = (datetime.now() - timedelta(seconds=1)).timestamp()
    conn.execute(
        "UPDATE user_island_structures SET timer_end = ? WHERE user_id = ? AND structure_key = ?",
        (past, user_id, structure_key),
    )
    conn.commit()


class IslandCatalogTests(unittest.TestCase):
    def test_core_cost_scales_with_target_level(self):
        stats = ISLAND_STRUCTURES["nucleo"]
        c1 = _structure_cost("nucleo", 0)
        c2 = _structure_cost("nucleo", 1)
        self.assertEqual(c1["target_level"], 1)
        self.assertEqual(c1["cost_saches"], stats["cost_saches_per_level"] * 1)
        self.assertEqual(c2["target_level"], 2)
        self.assertEqual(c2["cost_saches"], stats["cost_saches_per_level"] * 2)

    def test_non_core_cost_is_flat(self):
        stats = ISLAND_STRUCTURES["deposito"]
        c0 = _structure_cost("deposito", 0)
        self.assertEqual(c0["cost_saches"], stats["cost_saches"])
        self.assertEqual(c0["cost_scrap"], stats["cost_scrap"])


class GetIslandTests(unittest.TestCase):
    def test_creates_default_tier_0_island_on_first_access(self):
        conn = _make_conn()
        island = get_island(conn, 1)
        self.assertEqual(island["tier"], 0)

        # Segunda leitura não duplica a linha nem reseta o tier.
        island2 = get_island(conn, 1)
        self.assertEqual(island2["tier"], 0)
        rows = conn.execute("SELECT COUNT(*) c FROM user_islands WHERE user_id = ?", (1,)).fetchone()
        self.assertEqual(rows["c"], 1)


class StartConstructionTests(unittest.TestCase):
    def test_insufficient_resources_refuses_without_debiting(self):
        conn = _make_conn()
        user_id = 10
        modify_wallet(conn, user_id, 100, "Tester")  # bem menos que o custo do núcleo

        cost = _structure_cost("nucleo", 0)
        result = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "insufficient_resources")
        self.assertEqual(get_wallet(conn, user_id), 100)
        structures = get_island_structures(conn, user_id)
        self.assertNotIn("nucleo", structures)

    def test_successful_start_debits_resources_and_sets_building(self):
        conn = _make_conn()
        user_id = 11
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])

        result = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )

        self.assertTrue(result["success"])
        self.assertEqual(get_wallet(conn, user_id), 0)
        self.assertEqual(get_scrap(conn, user_id), 0)
        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "building")
        self.assertEqual(structures["nucleo"]["level"], 0)  # nível só sobe ao finalizar

    def test_cannot_start_again_while_already_building(self):
        conn = _make_conn()
        user_id = 12
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"] * 5, "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"] * 5)

        first = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertTrue(first["success"])
        wallet_after_first = get_wallet(conn, user_id)

        second = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertFalse(second["success"])
        self.assertEqual(second["reason"], "already_building")
        # Não cobra de novo por uma tentativa recusada.
        self.assertEqual(get_wallet(conn, user_id), wallet_after_first)


class FinalizeConstructionTests(unittest.TestCase):
    def _start_nucleo(self, conn, user_id):
        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        return cost

    def test_finalize_before_timer_ends_refuses(self):
        conn = _make_conn()
        user_id = 20
        self._start_nucleo(conn, user_id)

        result = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "not_ready")
        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "building")

    def test_finalize_after_timer_forced_ready_raises_level_and_tier(self):
        conn = _make_conn()
        user_id = 21
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")

        result = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["tier"], 1)

        structures = get_island_structures(conn, user_id)
        self.assertEqual(structures["nucleo"]["status"], "idle")
        self.assertEqual(structures["nucleo"]["level"], 1)

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)

        unlocks = get_island_unlocks(conn, user_id)
        self.assertIn("tier_1", unlocks)

    def test_double_finalize_only_applies_once(self):
        conn = _make_conn()
        user_id = 22
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")

        first = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(first["success"])

        second = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(second["success"])
        self.assertEqual(second["reason"], "not_building")

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)  # não subiu de novo

    def test_non_core_structure_finalize_does_not_change_island_tier(self):
        conn = _make_conn()
        user_id = 23
        # Núcleo precisa chegar a tier 1 antes de "deposito" (unlock_tier=1) liberar.
        self._start_nucleo(conn, user_id)
        _force_ready(conn, user_id, "nucleo")
        finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)

        cost = _structure_cost("deposito", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start_result = start_island_construction(
            conn, user_id, "deposito", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"],
            required_tier=ISLAND_STRUCTURES["deposito"]["unlock_tier"],
        )
        self.assertTrue(start_result["success"])
        _force_ready(conn, user_id, "deposito")

        result = finalize_island_construction(conn, user_id, "deposito", 1, is_core=False)
        self.assertTrue(result["success"])
        self.assertIsNone(result["tier"])

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 1)  # inalterado pelo depósito


class FullProgressionFlowTests(unittest.TestCase):
    def test_locked_structure_cannot_be_started_before_required_tier(self):
        conn = _make_conn()
        user_id = 30
        stats = ISLAND_STRUCTURES["deposito"]
        cost = _structure_cost("deposito", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])

        # O gate de tier é reavaliado dentro da própria transação atômica de
        # start_island_construction (não só na UI) — mesmo se alguém tentar
        # pular a checagem da view, o helper recusa e não debita nada.
        result = start_island_construction(
            conn,
            user_id,
            "deposito",
            cost["target_level"],
            cost["cost_saches"],
            cost["cost_scrap"],
            cost["build_hours"],
            required_tier=stats["unlock_tier"],
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")
        self.assertEqual(get_wallet(conn, user_id), cost["cost_saches"])  # nada debitado
        self.assertNotIn("deposito", get_island_structures(conn, user_id))

    def test_full_flow_create_build_wait_collect_tier_up(self):
        conn = _make_conn()
        user_id = 31

        island = get_island(conn, user_id)
        self.assertEqual(island["tier"], 0)

        cost = _structure_cost("nucleo", 0)
        modify_wallet(conn, user_id, cost["cost_saches"], "Tester")
        modify_scrap(conn, user_id, cost["cost_scrap"])
        start = start_island_construction(
            conn, user_id, "nucleo", cost["target_level"], cost["cost_saches"], cost["cost_scrap"], cost["build_hours"]
        )
        self.assertTrue(start["success"])

        # Ainda não passou o tempo: nada a coletar.
        premature = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertFalse(premature["success"])

        _force_ready(conn, user_id, "nucleo")
        done = finalize_island_construction(conn, user_id, "nucleo", 1, is_core=True)
        self.assertTrue(done["success"])
        self.assertEqual(get_island(conn, user_id)["tier"], 1)


if __name__ == "__main__":
    unittest.main()
