import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from config import CREATOR_ID
from permissions import is_bot_owner


class IsBotOwnerPredicateTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: is_bot_owner() é a fonte única de verdade de autorização
    admin. Aceita só CREATOR_ID; rejeita qualquer outro usuário, inclusive
    o "Dono" narrativo (ID_DONO) — são papéis distintos por decisão
    explícita, o admin é exclusivo do Criador.
    """

    async def test_predicate_accepts_creator(self):
        checked = is_bot_owner()

        @checked
        async def dummy(interaction):
            return "ok"

        checks = dummy.__discord_app_commands_checks__
        interaction = SimpleNamespace(user=SimpleNamespace(id=CREATOR_ID))
        for check in checks:
            self.assertTrue(await check(interaction))

    async def test_predicate_rejects_non_creator(self):
        checked = is_bot_owner()

        @checked
        async def dummy(interaction):
            return "ok"

        checks = dummy.__discord_app_commands_checks__
        interaction = SimpleNamespace(user=SimpleNamespace(id=541680099477422110))  # ID_DONO narrativo
        for check in checks:
            self.assertFalse(await check(interaction))

    async def test_predicate_rejects_arbitrary_user(self):
        checked = is_bot_owner()

        @checked
        async def dummy(interaction):
            return "ok"

        checks = dummy.__discord_app_commands_checks__
        interaction = SimpleNamespace(user=SimpleNamespace(id=1))
        for check in checks:
            self.assertFalse(await check(interaction))


class AdminGroupRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /admin economia consultar|corrigir estão registrados sob
    o subgrupo correto, com self vinculado (mesmo padrão de Group-como-
    atributo-de-Cog já validado em /mod) e com is_bot_owner() efetivamente
    anexado a cada comando.
    """

    async def test_admin_economia_subgroup_has_expected_commands_and_checks(self):
        from cogs.admin import AdminCog

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        cog = AdminCog(bot)
        await bot.add_cog(cog)

        admin = bot.tree.get_command("admin")
        self.assertIsNotNone(admin, "/admin não registrado na árvore")

        leaf_names = sorted(
            c.name for c in admin.walk_commands() if isinstance(c, discord.app_commands.Command)
        )
        self.assertEqual(leaf_names, ["consultar", "corrigir"])

        # Importante: verificar via a cópia registrada em bot.tree, não via
        # o atributo de classe original (AdminCog.economia_group) — a
        # injeção da Cog cria uma cópia com `binding` vinculado; a classe
        # original nunca é mutada e sempre teria binding=None.
        economia_subgroup = admin.get_command("economia")
        self.assertIsNotNone(economia_subgroup, "/admin economia não encontrado")

        for name in ("consultar", "corrigir"):
            cmd = economia_subgroup.get_command(name)
            self.assertIsNotNone(cmd, f"/admin economia {name} não encontrado")
            self.assertIs(cmd.binding, cog, f"/admin economia {name} sem binding correto de self")
            self.assertTrue(cmd.checks, f"/admin economia {name} sem is_bot_owner() anexado")

        self.assertEqual(admin.default_permissions, discord.Permissions(administrator=True))


class EconomiaCorrigirFlowTests(unittest.IsolatedAsyncioTestCase):
    """Testa o fluxo completo de /admin economia corrigir (não só o
    registro): recusar a confirmação não deve alterar o saldo; confirmar
    deve aplicar o delta corretamente via modify_wallet (camada v4).
    """

    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from economy_db import ensure_v4_tables

        # Schema legado completo: modify_wallet() sempre sincroniza de volta
        # para a tabela `economy` via sync_user_to_economy (mesmo padrão de
        # tests/test_economy_db.py).
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
            )
            """
        )
        ensure_v4_tables(conn)
        conn.execute("INSERT INTO users (user_id, user_name, wallet) VALUES (?, ?, ?)", (77, "Alvo", 500))
        conn.commit()
        return conn

    def _make_interaction(self, campo_value="wallet"):
        return SimpleNamespace(
            user=SimpleNamespace(id=CREATOR_ID),
            response=AsyncMock(),
            edit_original_response=AsyncMock(),
        )

    async def test_declined_confirmation_does_not_change_wallet(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=SimpleNamespace())
        interaction = self._make_interaction()
        usuario = SimpleNamespace(id=77, display_name="Alvo", mention="<@77>")
        campo = discord.app_commands.Choice(name="Carteira (wallet)", value="wallet")

        async def fake_wait(self_view):
            self_view.confirmed = False  # simula recusa/timeout

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("corrigir").callback(
                cog, interaction, usuario, campo, 999
            )

        row = conn.execute("SELECT wallet FROM users WHERE user_id = ?", (77,)).fetchone()
        self.assertEqual(row["wallet"], 500, "saldo não pode mudar quando a confirmação é recusada")

    async def test_confirmed_correction_applies_target_value(self):
        from cogs.admin import AdminCog, ConfirmView

        conn = self._make_conn()
        cog = AdminCog(bot=SimpleNamespace())
        interaction = self._make_interaction()
        usuario = SimpleNamespace(id=77, display_name="Alvo", mention="<@77>")
        campo = discord.app_commands.Choice(name="Carteira (wallet)", value="wallet")

        async def fake_wait(self_view):
            self_view.confirmed = True  # simula clique em "Confirmar"

        with patch.object(ConfirmView, "wait", fake_wait), \
             patch("cogs.admin.get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await AdminCog.economia_group.get_command("corrigir").callback(
                cog, interaction, usuario, campo, 999
            )

        row = conn.execute("SELECT wallet FROM users WHERE user_id = ?", (77,)).fetchone()
        self.assertEqual(row["wallet"], 999, "saldo deveria ter sido corrigido para o valor confirmado")


if __name__ == "__main__":
    unittest.main()
