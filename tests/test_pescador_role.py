"""Cargo automático "Pescador": concessão sob demanda + migração em massa.

A regra de desenho que os testes fixam é a de CUSTO: a verificação roda no
máximo uma vez por jogador, e um jogador já verificado não pode gerar nenhuma
consulta ao Discord. `/eco pescar` é o comando mais chamado do bot; se essa
garantia cair, cada lance passa a custar chamada de API para sempre.
"""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from economy_db import (
    ensure_user,
    ensure_v4_tables,
    mark_pescador_role_checked,
    pescador_role_checked,
)

# Importados pelo caminho do PACOTE, não pelos shims da raiz. O projeto tem
# dois objetos de módulo para cada arquivo — `pescador_role` (o shim, que faz
# `from src.p3luche.pescador_role import *`) e `src.p3luche.pescador_role` (o
# real) — e cada um tem sua própria cópia dos nomes importados. A função que
# roda de dentro do cog é a do módulo real, e é nos globais DELE que ela
# procura `log_to_gui`; patchear o shim substituiria uma cópia que ninguém
# chama. Mesmo raciocínio que o bootstrap de banco em tests/__init__.py
# aplica ao `config`.
from src.p3luche import migration_pescador_role as migracao
from src.p3luche import pescador_role
from src.p3luche.pescador_role import (
    ALREADY_HAD,
    ERROR,
    FORBIDDEN,
    GRANTED,
    NOT_A_MEMBER,
    ROLE_MISSING,
    SKIPPED,
    ensure_pescador_role,
)

ROLE_ID = 1457097764088447017
OUTRO_CARGO_ID = 999000111


def _pescar_conn():
    """Schema completo de pesca, reusado de test_economia.

    O nome do módulo depende de como a suíte foi invocada: o runner padrão do
    projeto (`unittest discover -s tests`) carrega os testes como módulos de
    topo (`test_economia`), enquanto `unittest tests.test_pescador_role`
    carrega pelo caminho do pacote. Tentar o nome de topo primeiro importa: no
    segundo caso, `from tests.test_economia import ...` reexecuta
    `tests/__init__.py`, que levanta de propósito quando o `config` já foi
    importado — a trava que impede a suíte de tocar o banco de produção.
    """
    try:
        from test_economia import _make_pescar_conn
    except ModuleNotFoundError:
        from tests.test_economia import _make_pescar_conn
    return _make_pescar_conn()


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_v4_tables(conn)
    return conn


# --------------------------------------------------------------- dublês
class FakeRole:
    def __init__(self, role_id=ROLE_ID, name="Pescador", position=5):
        self.id = role_id
        self.name = name
        self.position = position


class FakeMember:
    """Membro de servidor. `add_roles` conta chamadas e pode falhar."""

    def __init__(self, user_id, roles=(), falha=None):
        self.id = user_id
        self.roles = list(roles)
        self.guild = None
        self.falha = falha
        self.add_roles_calls = []

    async def add_roles(self, *roles, reason=None):
        self.add_roles_calls.append((roles, reason))
        if self.falha:
            raise self.falha
        self.roles.extend(roles)


class FakeGuild:
    def __init__(self, membros=(), role=None, name="Servidor de Teste",
                 manage_roles=True, bot_top_position=10):
        self.name = name
        self._role = FakeRole() if role is None else role
        self._membros = {}
        for m in membros:
            m.guild = self
            self._membros[m.id] = m
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=manage_roles),
            top_role=SimpleNamespace(name="P3LUCHE", position=bot_top_position),
        )
        self.get_role_calls = []

    def get_role(self, role_id):
        self.get_role_calls.append(role_id)
        if self._role is not None and self._role.id == role_id:
            return self._role
        return None

    def get_member(self, user_id):
        return self._membros.get(user_id)


def _http_error(status, texto="erro"):
    """discord.Forbidden/HTTPException exigem um response para construir."""
    resposta = MagicMock()
    resposta.status = status
    resposta.reason = texto
    if status == 403:
        return discord.Forbidden(resposta, texto)
    return discord.HTTPException(resposta, texto)


# --------------------------------------------------------------- coluna
class FlagColumnTests(unittest.TestCase):
    def test_defaults_to_false_for_a_new_player(self):
        conn = _make_conn()
        self.assertFalse(pescador_role_checked(conn, 1))

    def test_marking_is_persistent_and_idempotent(self):
        conn = _make_conn()
        ensure_user(conn, 2, "Tester")
        mark_pescador_role_checked(conn, 2)
        self.assertTrue(pescador_role_checked(conn, 2))
        mark_pescador_role_checked(conn, 2)
        self.assertTrue(pescador_role_checked(conn, 2))

    def test_alter_table_is_applied_to_a_pre_existing_database(self):
        """Banco criado antes da feature: a coluna entra por ALTER TABLE,
        mesmo padrão de daily_streak/last_memoria/forge_level."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                wallet INTEGER DEFAULT 0,
                fish_count INTEGER DEFAULT 0,
                guild_rank TEXT DEFAULT 'F',
                guild_xp INTEGER DEFAULT 0,
                scrap INTEGER DEFAULT 0
            );
            """
        )
        conn.execute("INSERT INTO users (user_id, user_name) VALUES (7, 'Antigo')")
        conn.commit()

        ensure_v4_tables(conn)

        colunas = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("pescador_role_checked", colunas)
        # Jogador que já existia entra como não verificado, não como verificado.
        self.assertFalse(pescador_role_checked(conn, 7))

    def test_running_ensure_twice_does_not_blow_up_on_duplicate_column(self):
        conn = _make_conn()
        ensure_v4_tables(conn)
        ensure_v4_tables(conn)
        self.assertFalse(pescador_role_checked(conn, 3))


# --------------------------------------------------- concessão sob demanda
class EnsurePescadorRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_player_receives_the_role(self):
        conn = _make_conn()
        membro = FakeMember(100)
        FakeGuild([membro])

        resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, GRANTED)
        self.assertEqual(len(membro.add_roles_calls), 1)
        self.assertTrue(pescador_role_checked(conn, 100))

    async def test_grant_carries_an_audit_log_reason(self):
        conn = _make_conn()
        membro = FakeMember(101)
        FakeGuild([membro])

        await ensure_pescador_role(conn, membro)

        _roles, reason = membro.add_roles_calls[0]
        self.assertEqual(reason, pescador_role.GRANT_REASON)

    async def test_existing_player_without_the_role_gets_it_and_the_flag_flips(self):
        conn = _make_conn()
        ensure_user(conn, 102, "Antigo")
        membro = FakeMember(102, roles=[FakeRole(OUTRO_CARGO_ID, "Outro")])
        FakeGuild([membro])
        self.assertFalse(pescador_role_checked(conn, 102))

        resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, GRANTED)
        self.assertTrue(pescador_role_checked(conn, 102))

    async def test_player_who_already_has_the_role_is_marked_without_granting(self):
        conn = _make_conn()
        membro = FakeMember(103, roles=[FakeRole()])
        FakeGuild([membro])

        resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, ALREADY_HAD)
        self.assertEqual(membro.add_roles_calls, [])
        self.assertTrue(pescador_role_checked(conn, 103))

    async def test_already_checked_player_makes_no_discord_call_at_all(self):
        """O invariante de custo. Nem get_role, nem leitura de roles, nem
        add_roles: um jogador já verificado sai antes de tocar no Discord."""
        conn = _make_conn()
        ensure_user(conn, 104, "Veterano")
        mark_pescador_role_checked(conn, 104)

        membro = FakeMember(104)
        guild = FakeGuild([membro])
        # Qualquer acesso a .roles neste membro é falha do teste.
        type(membro).roles = property(
            lambda self: (_ for _ in ()).throw(AssertionError("leu member.roles"))
        )
        try:
            resultado = await ensure_pescador_role(conn, membro)
        finally:
            del type(membro).roles

        self.assertEqual(resultado, SKIPPED)
        self.assertEqual(guild.get_role_calls, [], "consultou o cargo de um jogador ja verificado")
        self.assertEqual(membro.add_roles_calls, [])

    async def test_second_call_for_the_same_player_is_skipped(self):
        conn = _make_conn()
        membro = FakeMember(105)
        guild = FakeGuild([membro])

        primeiro = await ensure_pescador_role(conn, membro)
        chamadas_apos_primeiro = len(guild.get_role_calls)
        segundo = await ensure_pescador_role(conn, membro)

        self.assertEqual(primeiro, GRANTED)
        self.assertEqual(segundo, SKIPPED)
        self.assertEqual(len(membro.add_roles_calls), 1, "concedeu duas vezes")
        self.assertEqual(len(guild.get_role_calls), chamadas_apos_primeiro,
                         "consultou o cargo de novo na segunda chamada")

    async def test_missing_role_id_logs_an_error_and_does_not_burn_the_flag(self):
        """Se o ID do cargo mudar, consertar a constante tem que voltar a
        funcionar para todo mundo — marcar a flag aqui negaria o cargo para
        sempre a quem pescou durante a janela quebrada."""
        conn = _make_conn()
        membro = FakeMember(106)
        FakeGuild([membro], role=FakeRole(OUTRO_CARGO_ID, "Cargo Diferente"))

        with patch.object(pescador_role, "log_to_gui") as log:
            resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, ROLE_MISSING)
        self.assertFalse(pescador_role_checked(conn, 106))
        self.assertEqual(membro.add_roles_calls, [])
        mensagem, nivel = log.call_args.args
        self.assertEqual(nivel, "ERROR")
        self.assertIn(str(ROLE_ID), mensagem)
        self.assertIn("PESCADOR_ROLE_ID", mensagem)

    async def test_permission_error_is_logged_and_does_not_burn_the_flag(self):
        conn = _make_conn()
        membro = FakeMember(107, falha=_http_error(403, "Missing Permissions"))
        FakeGuild([membro])

        with patch.object(pescador_role, "log_to_gui") as log:
            resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, FORBIDDEN)
        self.assertFalse(pescador_role_checked(conn, 107))
        mensagem, nivel = log.call_args.args
        self.assertEqual(nivel, "ERROR")
        self.assertIn("hierarquia", mensagem)

    async def test_http_error_is_swallowed(self):
        conn = _make_conn()
        membro = FakeMember(108, falha=_http_error(500, "Server Error"))
        FakeGuild([membro])

        with patch.object(pescador_role, "log_to_gui"):
            resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, ERROR)
        self.assertFalse(pescador_role_checked(conn, 108))

    async def test_unexpected_exception_never_escapes(self):
        """Contrato do módulo: nada aqui pode subir para o fluxo de pesca."""
        conn = _make_conn()
        membro = FakeMember(109, falha=RuntimeError("catástrofe inesperada"))
        FakeGuild([membro])

        with patch.object(pescador_role, "log_to_gui"):
            resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, ERROR)

    async def test_without_a_guild_nothing_happens_and_the_flag_stays_false(self):
        """Fora de servidor (DM) não há cargo a conceder, e o mesmo jogador
        pescando no servidor depois ainda precisa ser verificado."""
        conn = _make_conn()
        membro = FakeMember(110)  # sem guild atribuída

        resultado = await ensure_pescador_role(conn, membro)

        self.assertEqual(resultado, NOT_A_MEMBER)
        self.assertFalse(pescador_role_checked(conn, 110))


# ---------------------------------------------- integração com /eco pescar
class PescarRoleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """O ponto de integração: a concessão acontece no fluxo de pesca e não
    pode nem duplicar captura nem impedir alguém de pescar."""

    def _make_conn(self):
        return _pescar_conn()

    def _make_interaction(self, membro):
        return SimpleNamespace(
            user=membro,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def _pescar(self, conn, membro):
        from cogs import economia

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await economia.pescar.callback(self._make_interaction(membro))

    async def test_first_fishing_cast_grants_the_role(self):
        conn = self._make_conn()
        membro = FakeMember(9301)
        membro.name = membro.display_name = "Novato"
        FakeGuild([membro])

        await self._pescar(conn, membro)

        self.assertEqual(len(membro.add_roles_calls), 1)
        self.assertTrue(pescador_role_checked(conn, 9301))

    async def test_permission_failure_does_not_stop_the_player_from_fishing(self):
        conn = self._make_conn()
        membro = FakeMember(9302, falha=_http_error(403, "Missing Permissions"))
        membro.name = membro.display_name = "Azarado"
        FakeGuild([membro])

        with patch.object(pescador_role, "log_to_gui"):
            await self._pescar(conn, membro)

        # A pescaria completou: o cooldown foi reservado e o embed foi enviado.
        cd = conn.execute(
            "SELECT last_fish FROM user_cooldowns WHERE user_id = ?", (9302,)
        ).fetchone()
        self.assertIsNotNone(cd["last_fish"])
        self.assertEqual(
            conn.execute("SELECT fish_count FROM users WHERE user_id = ?", (9302,)).fetchone()["fish_count"],
            1,
        )

    async def test_a_veteran_with_the_flag_set_costs_no_api_call_when_fishing(self):
        conn = self._make_conn()
        ensure_user(conn, 9303, "Veterano")
        mark_pescador_role_checked(conn, 9303)
        membro = FakeMember(9303)
        membro.name = membro.display_name = "Veterano"
        guild = FakeGuild([membro])

        await self._pescar(conn, membro)

        self.assertEqual(guild.get_role_calls, [])
        self.assertEqual(membro.add_roles_calls, [])


# ------------------------------------------------------- migração em massa
class MassMigrationPlanTests(unittest.TestCase):
    def _base(self, checados=()):
        """Banco com jogadores variados."""
        conn = _make_conn()
        for uid, nome, peixes in [
            (201, "TemCargo", 50),
            (202, "SemCargo", 30),
            (203, "SaiuDoServidor", 10),
            (204, "TambemSemCargo", 5),
        ]:
            ensure_user(conn, uid, nome)
            conn.execute("UPDATE users SET fish_count = ? WHERE user_id = ?", (peixes, uid))
        for uid in checados:
            mark_pescador_role_checked(conn, uid)
        conn.commit()
        return conn

    def _players(self, conn):
        return [
            {
                "user_id": r["user_id"],
                "user_name": r["user_name"],
                "fish_count": r["fish_count"],
                "already_checked": bool(r["pescador_role_checked"]),
            }
            for r in conn.execute(
                "SELECT user_id, user_name, fish_count, pescador_role_checked FROM users"
            )
        ]

    def _guild(self, **kwargs):
        """201 já tem o cargo, 202 e 204 não têm, 203 saiu do servidor."""
        return FakeGuild(
            [
                FakeMember(201, roles=[FakeRole()]),
                FakeMember(202, roles=[FakeRole(OUTRO_CARGO_ID, "Outro")]),
                FakeMember(204),
            ],
            **kwargs,
        )

    def test_plan_classifies_every_player(self):
        conn = self._base()
        plano = migracao.plan_from_guild(self._players(conn), self._guild())

        self.assertEqual(plano["total"], 4)
        self.assertEqual([p["user_id"] for p in plano[migracao.ALREADY_HAD]], [201])
        self.assertEqual(sorted(p["user_id"] for p in plano[migracao.WILL_RECEIVE]), [202, 204])
        self.assertEqual([p["user_id"] for p in plano[migracao.LEFT_SERVER]], [203])

    def test_plan_writes_nothing(self):
        """Dry-run é dry: nenhuma flag muda, nenhum cargo é concedido."""
        conn = self._base()
        guild = self._guild()
        migracao.plan_from_guild(self._players(conn), guild)

        for uid in (201, 202, 203, 204):
            self.assertFalse(pescador_role_checked(conn, uid))
        for membro in (guild.get_member(202), guild.get_member(204)):
            self.assertEqual(membro.add_roles_calls, [])

    def test_plan_reports_who_was_already_checked_before_this_pass(self):
        conn = self._base(checados=(201,))
        plano = migracao.plan_from_guild(self._players(conn), self._guild())
        self.assertEqual([p["user_id"] for p in plano["ja_checados"]], [201])

    def test_plan_refuses_when_the_role_does_not_exist(self):
        conn = self._base()
        guild = FakeGuild([], role=FakeRole(OUTRO_CARGO_ID, "Outro"))
        with self.assertRaises(migracao.PreflightFailed):
            migracao.plan_from_guild(self._players(conn), guild)

    def test_plan_survives_a_database_without_the_column_yet(self):
        """O script pode rodar antes do primeiro restart do bot com a feature.
        Nesse momento a coluna ainda não existe (ela entra por ALTER TABLE em
        ensure_v4_tables), e o dry-run não pode nem estourar nem criá-la — a
        conexão dele é somente leitura de propósito.
        """
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "antigo.db")
            antigo = sqlite3.connect(caminho)
            antigo.executescript(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    user_name TEXT,
                    wallet INTEGER DEFAULT 0,
                    fish_count INTEGER DEFAULT 0,
                    guild_rank TEXT DEFAULT 'F',
                    guild_xp INTEGER DEFAULT 0,
                    scrap INTEGER DEFAULT 0
                );
                """
            )
            antigo.execute("INSERT INTO users (user_id, user_name, fish_count) VALUES (201, 'TemCargo', 50)")
            antigo.execute("INSERT INTO users (user_id, user_name, fish_count) VALUES (202, 'SemCargo', 30)")
            antigo.commit()
            antigo.close()

            players = migracao.load_players(caminho)

            self.assertEqual(len(players), 2)
            self.assertTrue(all(p["coluna_ausente"] for p in players))
            self.assertFalse(any(p["already_checked"] for p in players))

            plano = migracao.plan_from_guild(players, self._guild())
            self.assertTrue(plano["coluna_ausente"])
            self.assertEqual([p["user_id"] for p in plano[migracao.ALREADY_HAD]], [201])
            self.assertEqual([p["user_id"] for p in plano[migracao.WILL_RECEIVE]], [202])

            # O dry-run não criou a coluna.
            conferencia = sqlite3.connect(caminho)
            colunas = {r[1] for r in conferencia.execute("PRAGMA table_info(users)")}
            conferencia.close()
            self.assertNotIn("pescador_role_checked", colunas)
    def test_preflight_flags_a_missing_manage_roles_permission(self):
        check = migracao.preflight(self._guild(manage_roles=False))
        self.assertFalse(check["ok"])
        self.assertTrue(any("Gerenciar Cargos" in p for p in check["problemas"]))

    def test_preflight_flags_a_role_above_the_bot_in_the_hierarchy(self):
        check = migracao.preflight(self._guild(bot_top_position=1))
        self.assertFalse(check["ok"])
        self.assertTrue(any("acima ou igual" in p for p in check["problemas"]))

    def test_preflight_passes_on_a_healthy_server(self):
        self.assertTrue(migracao.preflight(self._guild())["ok"])


class MassMigrationApplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        base = MassMigrationPlanTests()
        self.conn = base._base()
        self.players = base._players(self.conn)
        self._guild_factory = base._guild

    async def test_apply_grants_only_to_who_needs_it(self):
        guild = self._guild_factory()
        plano = migracao.plan_from_guild(self.players, guild)

        resultado = await migracao.apply_plan(self.conn, guild, plano)

        self.assertEqual(sorted(p["user_id"] for p in resultado["concedidos"]), [202, 204])
        self.assertEqual(resultado["falhas"], [])
        self.assertEqual(len(guild.get_member(201).add_roles_calls), 0, "concedeu a quem ja tinha")
        self.assertEqual(len(guild.get_member(202).add_roles_calls), 1)
        self.assertEqual(len(guild.get_member(204).add_roles_calls), 1)

    async def test_apply_marks_the_flag_for_everyone_including_who_left(self):
        """Inclusive quem saiu do servidor: a condição não se resolve sozinha,
        e reprocessá-la em toda execução não levaria a nada."""
        guild = self._guild_factory()
        plano = migracao.plan_from_guild(self.players, guild)

        resultado = await migracao.apply_plan(self.conn, guild, plano)

        self.assertEqual(resultado["marcados"], 4)
        for uid in (201, 202, 203, 204):
            with self.subTest(user_id=uid):
                self.assertTrue(pescador_role_checked(self.conn, uid))

    async def test_a_single_failure_does_not_stop_the_rest_and_is_reported(self):
        guild = self._guild_factory()
        guild.get_member(202).falha = _http_error(403, "Missing Permissions")
        plano = migracao.plan_from_guild(self.players, guild)

        resultado = await migracao.apply_plan(self.conn, guild, plano)

        self.assertEqual([p["user_id"] for p in resultado["falhas"]], [202])
        self.assertEqual([p["user_id"] for p in resultado["concedidos"]], [204])
        # Falha também é marcada por este script (diferente do fluxo de pesca).
        self.assertTrue(pescador_role_checked(self.conn, 202))
        self.assertIn("permissao/hierarquia", resultado["falhas"][0]["erro"])

    async def test_apply_refuses_on_a_misconfigured_server_without_burning_flags(self):
        """A trava que protege a base: sem permissão, o script recusa em vez de
        gastar a única verificação de todos os jogadores sem conceder nada."""
        guild = self._guild_factory(manage_roles=False)
        plano = migracao.plan_from_guild(self.players, guild)

        with self.assertRaises(migracao.PreflightFailed):
            await migracao.apply_plan(self.conn, guild, plano)

        for uid in (201, 202, 203, 204):
            with self.subTest(user_id=uid):
                self.assertFalse(pescador_role_checked(self.conn, uid))
        self.assertEqual(guild.get_member(202).add_roles_calls, [])

    async def test_apply_creates_the_column_when_it_is_missing(self):
        """O --apply grava, então ele PODE (e precisa) criar a coluna antes."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                wallet INTEGER DEFAULT 0,
                fish_count INTEGER DEFAULT 0,
                guild_rank TEXT DEFAULT 'F',
                guild_xp INTEGER DEFAULT 0,
                scrap INTEGER DEFAULT 0
            );
            """
        )
        for uid, nome in [(201, "TemCargo"), (202, "SemCargo")]:
            conn.execute("INSERT INTO users (user_id, user_name) VALUES (?, ?)", (uid, nome))
        conn.commit()

        players = [
            {"user_id": 201, "user_name": "TemCargo", "fish_count": 0, "already_checked": False},
            {"user_id": 202, "user_name": "SemCargo", "fish_count": 0, "already_checked": False},
        ]
        guild = self._guild_factory()
        plano = migracao.plan_from_guild(players, guild)

        resultado = await migracao.apply_plan(conn, guild, plano)

        colunas = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("pescador_role_checked", colunas)
        self.assertEqual(resultado["marcados"], 2)
        self.assertTrue(pescador_role_checked(conn, 201))
        self.assertTrue(pescador_role_checked(conn, 202))
    async def test_running_twice_is_harmless(self):
        guild = self._guild_factory()
        plano = migracao.plan_from_guild(self.players, guild)
        await migracao.apply_plan(self.conn, guild, plano)

        # Segundo passe: todos já verificados, ninguém mais para receber.
        players_2 = MassMigrationPlanTests()._players(self.conn)
        plano_2 = migracao.plan_from_guild(players_2, guild)
        resultado_2 = await migracao.apply_plan(self.conn, guild, plano_2)

        self.assertEqual(resultado_2["concedidos"], [])
        self.assertEqual(resultado_2["falhas"], [])
        self.assertEqual(len(guild.get_member(202).add_roles_calls), 1, "concedeu de novo")

    async def test_member_who_leaves_between_plan_and_apply_becomes_a_failure(self):
        guild = self._guild_factory()
        plano = migracao.plan_from_guild(self.players, guild)
        del guild._membros[204]  # saiu do servidor no meio do passe

        resultado = await migracao.apply_plan(self.conn, guild, plano)

        self.assertEqual([p["user_id"] for p in resultado["falhas"]], [204])
        self.assertEqual([p["user_id"] for p in resultado["concedidos"]], [202])
        self.assertTrue(pescador_role_checked(self.conn, 204))


class RootShimTests(unittest.TestCase):
    """O cog importa `from pescador_role import ensure_pescador_role`, o que
    resolve pelo shim da raiz. Sem o shim, economia.py nem carrega — foi
    exatamente assim que a suíte quebrou quando o módulo novo entrou só em
    src/p3luche/."""

    def test_root_shim_reexports_the_entry_point(self):
        import pescador_role as shim

        self.assertIs(shim.ensure_pescador_role, ensure_pescador_role)

    def test_root_shim_exists_for_the_migration_too(self):
        import migration_pescador_role as shim

        self.assertIs(shim.plan_from_guild, migracao.plan_from_guild)

    def test_the_cog_calls_the_real_module(self):
        from cogs import economia

        self.assertIs(economia.ensure_pescador_role, ensure_pescador_role)


class MassMigrationReportTests(unittest.TestCase):
    """O relatório é o que vai ser revisado antes de aplicar em produção —
    ele precisa mostrar os quatro números, não apenas não quebrar."""

    def test_dry_run_report_shows_every_bucket(self):
        import io
        from contextlib import redirect_stdout

        base = MassMigrationPlanTests()
        conn = base._base()
        plano = migracao.plan_from_guild(base._players(conn), base._guild())

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            migracao.print_report(plano)
        saida = buffer.getvalue()

        self.assertIn("DRY-RUN", saida)
        self.assertIn("Total de jogadores em users:   4", saida)
        self.assertIn("Ja tinham o cargo:             1", saida)
        self.assertIn("Vao receber agora:", saida)
        self.assertIn("Fora do servidor:              1", saida)
        self.assertIn("Nada foi gravado", saida)


if __name__ == "__main__":
    unittest.main()
