import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from config import CREATOR_ID
from economy_db import (
    add_inventory_item,
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_current_rod,
    get_inventory,
    get_island,
    get_rod_upgrades,
    get_scrap,
    get_wallet,
    modify_scrap,
    modify_wallet,
    reset_all_players,
    reset_player_progress,
    set_cooldown,
    set_current_rod,
    start_island_construction,
    try_upgrade_rod,
)


def _make_full_conn():
    """Schema completo (legado `economy` + auxiliares + tabelas v4) — os
    resets tocam tanto a legada quanto a v4, então o teste precisa de ambas,
    igual ao fixture já usado em tests/test_economia.py."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
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
        );
        CREATE TABLE quest_progress (
            user_id INTEGER PRIMARY KEY,
            current_chapter TEXT DEFAULT 'inicio',
            quest_status TEXT DEFAULT 'locked',
            inventory TEXT DEFAULT '{}',
            reputation INTEGER DEFAULT 0,
            updated_at TIMESTAMP
        );
        CREATE TABLE parties (
            leader_id INTEGER PRIMARY KEY,
            leader_name TEXT,
            members_json TEXT DEFAULT '[]',
            active_mission_id TEXT,
            mission_progress INTEGER DEFAULT 0,
            mission_target INTEGER DEFAULT 0,
            created_at TIMESTAMP
        );
        CREATE TABLE persistent_catches (
            user_id INTEGER PRIMARY KEY,
            catch_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP
        );
        CREATE TABLE world_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_weather TEXT DEFAULT 'normal',
            weather_end TIMESTAMP
        );
        """
    )
    conn.execute("INSERT INTO world_state (id, current_weather) VALUES (1, 'normal')")
    ensure_v4_tables(conn)
    conn.commit()
    return conn


def _seed_full_progress(conn, user_id: int, name: str = "Tester"):
    """Deixa um jogador com progresso em TODAS as áreas que um reset toca,
    pra garantir que cada campo realmente muda."""
    ensure_user(conn, user_id, name)
    modify_wallet(conn, user_id, 5000, name)
    modify_scrap(conn, user_id, 500)
    add_inventory_item(conn, user_id, "isca", 3)
    set_current_rod(conn, user_id, "vara_ouro")
    try_upgrade_rod(conn, user_id, "luck", cost_per_level=1)
    try_upgrade_rod(conn, user_id, "cd", cost_per_level=1)
    set_cooldown(conn, user_id, "last_fish", "2026-01-01 00:00:00.000000")
    set_cooldown(conn, user_id, "last_daily", "2026-01-01 00:00:00.000000")
    set_cooldown(conn, user_id, "last_explore", "2026-01-01 00:00:00.000000")
    conn.execute("UPDATE users SET guild_rank = 'C', guild_xp = 900, fish_count = 42 WHERE user_id = ?", (user_id,))
    conn.execute(
        "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'garrafa_encontrada') "
        "ON CONFLICT(user_id) DO UPDATE SET current_chapter = excluded.current_chapter",
        (user_id,),
    )
    conn.execute(
        "INSERT INTO persistent_catches (user_id, catch_count) VALUES (?, 7) "
        "ON CONFLICT(user_id) DO UPDATE SET catch_count = excluded.catch_count",
        (user_id,),
    )
    conn.execute("INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?, 'primeira_pesca')", (user_id,))
    conn.execute(
        "INSERT OR IGNORE INTO tournament_leaderboard (user_id, tournament_id, points) VALUES (?, 'verao_2026', 50)",
        (user_id,),
    )
    start_island_construction(conn, user_id, "nucleo", 1, 0, 0, 1)
    conn.commit()


class ResetPlayerProgressTests(unittest.TestCase):
    def test_zeroes_wallet_scrap_inventory_rod_and_upgrades(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 1)

        reset_player_progress(conn, 1)

        self.assertEqual(get_wallet(conn, 1), 0)
        self.assertEqual(get_scrap(conn, 1), 0)
        self.assertEqual(get_inventory(conn, 1), {})
        self.assertEqual(get_current_rod(conn, 1), "vara_bambu")
        upgrades = get_rod_upgrades(conn, 1)
        self.assertEqual(upgrades["luck"], 0)
        self.assertEqual(upgrades["cd"], 0)

    def test_zeroes_cooldowns(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 2)

        reset_player_progress(conn, 2)

        cd = get_cooldowns(conn, 2)
        self.assertIsNone(cd["last_fish"])
        self.assertIsNone(cd["last_daily"])
        self.assertIsNone(cd["last_explore"])
        self.assertEqual(cd["daily_streak"], 0)

    def test_zeroes_island_tier_and_deletes_structures(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 3)
        self.assertEqual(get_island(conn, 3)["tier"], 0)  # ainda não finalizado, só "building"

        reset_player_progress(conn, 3)

        self.assertEqual(get_island(conn, 3)["tier"], 0)
        structs = conn.execute("SELECT COUNT(*) c FROM user_island_structures WHERE user_id = ?", (3,)).fetchone()
        self.assertEqual(structs["c"], 0)

    def test_resets_quest_and_fish_count_and_guild_rank(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 4)

        reset_player_progress(conn, 4)

        row = conn.execute("SELECT fish_count, guild_rank, guild_xp FROM users WHERE user_id = ?", (4,)).fetchone()
        self.assertEqual(row["fish_count"], 0)
        self.assertEqual(row["guild_rank"], "F")
        self.assertEqual(row["guild_xp"], 0)
        quest = conn.execute("SELECT current_chapter FROM quest_progress WHERE user_id = ?", (4,)).fetchone()
        self.assertEqual(quest["current_chapter"], "inicio")

    def test_does_not_touch_achievements(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 5)

        reset_player_progress(conn, 5)

        ach = conn.execute("SELECT COUNT(*) c FROM achievements WHERE user_id = ?", (5,)).fetchone()
        self.assertEqual(ach["c"], 1, "conquistas são registro permanente, reset individual não deve apagar")

    def test_deletes_tournament_leaderboard_entries(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 55)

        reset_player_progress(conn, 55)

        rows = conn.execute(
            "SELECT COUNT(*) c FROM tournament_leaderboard WHERE user_id = ?", (55,)
        ).fetchone()
        self.assertEqual(rows["c"], 0, "pontos de torneio vêm de atividade econômica, devem zerar junto")

    def test_keeps_the_row_does_not_delete_account(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 6)

        reset_player_progress(conn, 6)

        row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (6,)).fetchone()
        self.assertIsNotNone(row, "reset individual não deleta a linha do jogador")

    def test_only_touches_the_target_user(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 7)
        _seed_full_progress(conn, 8)

        reset_player_progress(conn, 7)

        self.assertEqual(get_wallet(conn, 7), 0)
        self.assertGreater(get_wallet(conn, 8), 0, "reset individual não pode afetar outro jogador")


class ResetAllPlayersTests(unittest.TestCase):
    def test_zeroes_singleton_fields_for_every_player(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 10)
        _seed_full_progress(conn, 11)

        result = reset_all_players(conn)

        self.assertTrue(result["success"])
        self.assertEqual(result["players_affected"], 2)
        for uid in (10, 11):
            self.assertEqual(get_wallet(conn, uid), 0)
            self.assertEqual(get_scrap(conn, uid), 0)
            self.assertEqual(get_current_rod(conn, uid), "vara_bambu")

    def test_deletes_collection_tables_entirely(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 12)

        reset_all_players(conn)

        for table in ("user_inventory", "achievements", "user_island_structures", "parties"):
            row = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()
            self.assertEqual(row["c"], 0, f"{table} deveria estar vazia após reset global")

    def test_keeps_rows_in_singleton_tables(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 13)

        reset_all_players(conn)

        row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (13,)).fetchone()
        self.assertIsNotNone(row, "reset global não deleta as linhas de users, só zera campos")

    def test_does_not_touch_fish_sales_history(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 14)
        conn.execute(
            "INSERT INTO fish_sales_history (fish_name, sale_price, user_id) VALUES ('Sardinha', 20, 14)"
        )
        conn.commit()

        reset_all_players(conn)

        row = conn.execute("SELECT COUNT(*) c FROM fish_sales_history").fetchone()
        self.assertEqual(row["c"], 1, "ledger de vendas é histórico, não estado do jogador")

    def test_legacy_economy_table_is_reset_too(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 15)

        reset_all_players(conn)

        row = conn.execute("SELECT wallet, inventory, current_rod FROM economy WHERE user_id = ?", (15,)).fetchone()
        self.assertEqual(row["wallet"], 0)
        self.assertEqual(row["inventory"], "{}")
        self.assertEqual(row["current_rod"], "vara_bambu")


def _make_fake_bot():
    fake_channel = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(get_channel=lambda cid: fake_channel)


def _make_interaction(user_id=CREATOR_ID):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        response=AsyncMock(),
        edit_original_response=AsyncMock(),
        followup=AsyncMock(),
    )


class EconomiaDarRemoverTests(unittest.IsolatedAsyncioTestCase):
    def _make_conn(self):
        conn = _make_full_conn()
        modify_wallet(conn, 50, 1000, "Alvo")
        return conn

    async def test_dar_declined_does_not_change_wallet(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=50, display_name="Alvo", mention="<@50>")

        async def fake_wait(self_view):
            self_view.confirmed = False

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("dar").callback(cog, interaction, usuario, 200)

        self.assertEqual(get_wallet(conn, 50), 1000)

    async def test_dar_confirmed_adds_value(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=50, display_name="Alvo", mention="<@50>")

        async def fake_wait(self_view):
            self_view.confirmed = True

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("dar").callback(cog, interaction, usuario, 200)

        self.assertEqual(get_wallet(conn, 50), 1200)

    async def test_dar_rejects_non_positive_value_without_confirmation_prompt(self):
        from cogs.admin import AdminCog

        conn = self._make_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=50, display_name="Alvo", mention="<@50>")

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("dar").callback(cog, interaction, usuario, 0)

        self.assertEqual(get_wallet(conn, 50), 1000)
        interaction.response.send_message.assert_awaited_once()

    async def test_remover_confirmed_subtracts_value(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=50, display_name="Alvo", mention="<@50>")

        async def fake_wait(self_view):
            self_view.confirmed = True

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("remover").callback(cog, interaction, usuario, 300)

        self.assertEqual(get_wallet(conn, 50), 700)

    async def test_remover_declined_does_not_change_wallet(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=50, display_name="Alvo", mention="<@50>")

        async def fake_wait(self_view):
            self_view.confirmed = False

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("remover").callback(cog, interaction, usuario, 300)

        self.assertEqual(get_wallet(conn, 50), 1000)


class EconomiaResetarIndividualTests(unittest.IsolatedAsyncioTestCase):
    async def test_declined_does_not_reset(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = _make_full_conn()
        _seed_full_progress(conn, 60)
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=60, display_name="Alvo", mention="<@60>")

        async def fake_wait(self_view):
            self_view.confirmed = False

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("resetar").callback(cog, interaction, usuario)

        self.assertGreater(get_wallet(conn, 60), 0)

    async def test_confirmed_resets_progress_and_logs_audit(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = _make_full_conn()
        _seed_full_progress(conn, 61)
        bot = _make_fake_bot()
        cog = AdminCog(bot=bot)
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=61, display_name="Alvo", mention="<@61>")

        async def fake_wait(self_view):
            self_view.confirmed = True

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("resetar").callback(cog, interaction, usuario)

        self.assertEqual(get_wallet(conn, 61), 0)
        bot.get_channel(0).send.assert_awaited_once()


class SistemaFixCooldownsTests(unittest.IsolatedAsyncioTestCase):
    async def test_clears_cooldowns_in_legacy_and_v4_tables(self):
        from cogs.admin import AdminCog

        conn = _make_full_conn()
        _seed_full_progress(conn, 70)
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.sistema_group.get_command("fix_cooldowns").callback(cog, interaction)

        cd = get_cooldowns(conn, 70)
        self.assertIsNone(cd["last_fish"])
        self.assertIsNone(cd["last_explore"])
        legacy = conn.execute("SELECT last_fish, last_explore FROM economy WHERE user_id = ?", (70,)).fetchone()
        self.assertIsNone(legacy["last_fish"])
        self.assertIsNone(legacy["last_explore"])


class DebugCatchesTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_single_user_only_removes_that_entry(self):
        from cogs.admin import AdminCog
        from config import CATCHES_SINCE_RESTART

        CATCHES_SINCE_RESTART.clear()
        CATCHES_SINCE_RESTART[80] = (3, 0.0)
        CATCHES_SINCE_RESTART[81] = (5, 0.0)
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=80)
        acao = discord.app_commands.Choice(name="Resetar", value="resetar")

        try:
            await AdminCog.debug_group.get_command("catches").callback(cog, interaction, acao, usuario)
            self.assertNotIn(80, CATCHES_SINCE_RESTART)
            self.assertIn(81, CATCHES_SINCE_RESTART)
        finally:
            CATCHES_SINCE_RESTART.clear()

    async def test_inspect_does_not_mutate_counters(self):
        from cogs.admin import AdminCog
        from config import CATCHES_SINCE_RESTART
        import time

        CATCHES_SINCE_RESTART.clear()
        CATCHES_SINCE_RESTART[82] = (9, time.time())
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        acao = discord.app_commands.Choice(name="Inspecionar", value="inspecionar")

        try:
            await AdminCog.debug_group.get_command("catches").callback(cog, interaction, acao, None)
            self.assertIn(82, CATCHES_SINCE_RESTART)
            interaction.response.send_message.assert_awaited_once()
        finally:
            CATCHES_SINCE_RESTART.clear()


class DebugQuestTests(unittest.IsolatedAsyncioTestCase):
    async def test_grants_bottle_to_caller(self):
        from cogs.admin import AdminCog

        conn = _make_full_conn()
        ensure_user(conn, 90, "Tester")
        conn.execute("INSERT OR IGNORE INTO economy (user_id, inventory) VALUES (90, '{}')")
        conn.commit()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction(user_id=90)

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.debug_group.get_command("quest").callback(cog, interaction)

        row = conn.execute("SELECT inventory FROM economy WHERE user_id = ?", (90,)).fetchone()
        self.assertIn("garrafa_incrustada", row["inventory"])


class DebugInspecionarTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_at_least_one_parameter(self):
        from cogs.admin import AdminCog

        conn = _make_full_conn()
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.debug_group.get_command("inspecionar").callback(cog, interaction, None, None)

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        self.assertIn("Informe", args[0] if args else kwargs.get("content", ""))

    async def test_dump_user_does_not_mutate_state(self):
        from cogs.admin import AdminCog

        conn = _make_full_conn()
        _seed_full_progress(conn, 91)
        before = get_wallet(conn, 91)
        cog = AdminCog(bot=_make_fake_bot())
        interaction = _make_interaction()
        usuario = SimpleNamespace(id=91, display_name="Alvo")

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.debug_group.get_command("inspecionar").callback(cog, interaction, usuario, None)

        self.assertEqual(get_wallet(conn, 91), before)
        interaction.response.send_message.assert_awaited_once()


class ResetGlobalModalFlowTests(unittest.IsolatedAsyncioTestCase):
    """Fluxo completo do comando mais perigoso do projeto: texto de
    confirmação errado não altera nada; confirmação certa mas backup falho
    aborta sem alterar nada; confirmação certa + backup ok executa e loga.
    """

    def _make_modal(self, bot, players_affected=2):
        from cogs.admin import ResetGlobalModal

        executor = SimpleNamespace(id=CREATOR_ID, mention=f"<@{CREATOR_ID}>")
        modal = ResetGlobalModal(bot, executor, players_affected)
        return modal

    def _seeded_conn(self):
        conn = _make_full_conn()
        _seed_full_progress(conn, 100)
        _seed_full_progress(conn, 101)
        return conn

    async def test_wrong_confirmation_text_aborts_without_touching_db(self):
        conn = self._seeded_conn()
        bot = _make_fake_bot()
        modal = self._make_modal(bot, players_affected=2)
        modal.confirm_text._value = "numero errado"  # TextInput.value é read-only; setter interno usado só em teste
        interaction = SimpleNamespace(response=AsyncMock())

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch("cogs.admin._upload_db_sync") as fake_upload:
            await modal.on_submit(interaction)

        fake_upload.assert_not_called()
        self.assertEqual(get_wallet(conn, 100), 5000)
        interaction.response.send_message.assert_awaited_once()

    async def test_backup_failure_aborts_reset_and_logs_abort(self):
        conn = self._seeded_conn()
        bot = _make_fake_bot()
        modal = self._make_modal(bot, players_affected=2)
        modal.confirm_text._value = "2"  # TextInput.value é read-only; setter interno usado só em teste
        interaction = SimpleNamespace(response=AsyncMock(), followup=AsyncMock())

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch("cogs.admin._upload_db_sync", side_effect=RuntimeError("drive indisponível")):
            await modal.on_submit(interaction)

        # Nada foi alterado: backup falhou antes do reset rodar.
        self.assertEqual(get_wallet(conn, 100), 5000)
        self.assertEqual(get_wallet(conn, 101), 5000)
        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args.args[0] if interaction.followup.send.call_args.args \
            else interaction.followup.send.call_args.kwargs.get("content", "")
        self.assertIn("ABORTADO", sent_text)
        # Log de auditoria do abort foi enviado.
        bot.get_channel(0).send.assert_awaited_once()

    async def test_backup_success_executes_reset_and_logs_success(self):
        conn = self._seeded_conn()
        bot = _make_fake_bot()
        modal = self._make_modal(bot, players_affected=2)
        modal.confirm_text._value = "2"  # TextInput.value é read-only; setter interno usado só em teste
        interaction = SimpleNamespace(response=AsyncMock(), followup=AsyncMock())

        with patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch("cogs.admin._upload_db_sync", return_value="https://drive.example/backup.db"):
            await modal.on_submit(interaction)

        self.assertEqual(get_wallet(conn, 100), 0)
        self.assertEqual(get_wallet(conn, 101), 0)
        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args.args[0] if interaction.followup.send.call_args.args \
            else interaction.followup.send.call_args.kwargs.get("content", "")
        self.assertIn("concluído", sent_text)
        bot.get_channel(0).send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
