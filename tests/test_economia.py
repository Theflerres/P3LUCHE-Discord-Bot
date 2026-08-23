import asyncio
import json
import random
import sqlite3
import unittest
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs import economia
from economy_db import (
    FORGE_BASE_COST,
    FORGE_GROWTH,
    FORGE_REQUIRED_RANK,
    MISSION_DAILY_CAP,
    add_guild_xp,
    forge_level_cost,
    forge_luck_multiplier,
    get_forge_level,
    set_forge_level,
    try_upgrade_forge,
    add_inventory_item,
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_current_rod,
    get_guild_rank,
    get_inventory,
    get_rod_upgrades,
    get_scrap,
    get_trap,
    get_wallet,
    get_top_players,
    get_user_names,
    modify_scrap,
    modify_wallet,
    set_cooldown,
    set_current_rod,
    set_guild_rank,
    set_inventory_item,
    set_trap,
    sync_user_to_economy,
    try_register_mission_completion,
    try_spend_wallet,
    mission_slots_left,
    missions_completed_today,
)


def _make_pescar_conn():
    """Schema completo (tabela legada `economy` + tabelas v4) usado pelos
    testes de pesca — mistura o que `test_economy_db.py` já usa com as
    tabelas auxiliares (quest_progress, parties, persistent_catches,
    world_state) que `pescar()`/`_finalize_pescar` também tocam.
    """
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
            rod_tier INTEGER DEFAULT 0,
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
        CREATE TABLE fish_sales_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fish_name TEXT NOT NULL,
            sale_price INTEGER NOT NULL,
            user_id INTEGER,
            sale_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute("INSERT INTO world_state (id, current_weather) VALUES (1, 'normal')")
    ensure_v4_tables(conn)
    conn.commit()
    return conn


class FinalizePescarDeltaTests(unittest.IsolatedAsyncioTestCase):
    """Regressão para o fix de duplicação de saldo/inventário na pesca.

    Cenário: o snapshot (`inv_before`) é capturado no início de `/eco pescar`,
    mas a escrita final só acontece depois — e há awaits no meio. Se, nessa
    janela, outro comando (ex.: /eco comprar, já migrado pra v4) alterar
    saldo/inventário no banco, `_finalize_pescar` NÃO pode sobrescrever essa
    mudança com o snapshot antigo — precisa aplicar a pescaria como delta em
    cima do estado v4 fresco (via modify_wallet/add_inventory_item).

    (A janela era bem maior quando o QTE existia, mas o fix não dependia dele:
    qualquer await entre a leitura e a gravação reabre a mesma corrida.)
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(name=name),
            followup=SimpleNamespace(send=AsyncMock()),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

    async def test_external_purchase_during_open_catch_is_not_reverted(self):
        conn = self._make_conn()
        user_id = 42

        # Estado inicial, no momento em que /eco pescar lê o usuário.
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")
        add_inventory_item(conn, user_id, "isca", 5)

        # Snapshot capturado no início de pescar().
        inv_before = {"isca": 5}
        # Estado local após o consumo de 1 isca pela tentativa de pesca
        # (mutação em memória feita antes da gravação, como no código real).
        inv_after_local = {"isca": 4}

        # --- Ação externa antes da gravação final (ex: /eco comprar, v4) ---
        # Gasta 300 e ganha um item novo; NÃO mexe em "isca".
        modify_wallet(conn, user_id, -300)
        add_inventory_item(conn, user_id, "novo_item", 1)

        ctx = {
            "user_id": user_id,
            "inv": inv_after_local,
            "inv_before": inv_before,
            "valor": 300,
            "nome": "Tubarão Martelo",
            "emoji": "🔨",
            "tier_p": 2,
            "frase": "Pregos não inclusos.",
            "rod_data": {"name": "Vara Teste", "luck": 1},
            "actual_cd": 300,
            "mission_msg": "",
            "mission_completed": False,
            "quest_trigger": False,
            "new_xp_total": 0,
            "current_rank": "F",
            "used_bait": True,
            "agora_str": "2026-08-13 12:00:00.000000",
            "w_key": "normal",
            "w_stats": {"name": "Normal", "luck_mod": 1},
            "is_trash": False,
        }

        interaction = self._make_interaction()
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia._finalize_pescar(interaction, ctx)

        # O gasto externo (300) continua valendo: saldo = 700 (pós-compra) + 300 (peixe).
        # Sem o fix, seria 1000 (saldo antigo do snapshot) + 300 = 1300, revertendo a compra.
        self.assertEqual(get_wallet(conn, user_id), 1000)
        final_inv = get_inventory(conn, user_id)
        # O item comprado durante a captura em aberto não pode desaparecer.
        self.assertEqual(final_inv.get("novo_item"), 1)
        # A isca consumida pela pescaria (delta -1) é aplicada sobre o estado
        # fresco (5, não o snapshot antigo), resultando em 4 — não em "resetar"
        # para o valor local antigo e perder o "novo_item" no processo.
        self.assertEqual(final_inv.get("isca"), 4)

        # A tabela legada `economy` (ainda lida por /eco saldo, /eco rank...)
        # precisa continuar em sincronia com o estado v4.
        legacy = conn.execute(
            "SELECT wallet, inventory FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual(legacy["wallet"], 1000)
        legacy_inv = json.loads(legacy["inventory"])
        self.assertEqual(legacy_inv.get("novo_item"), 1)
        self.assertEqual(legacy_inv.get("isca"), 4)

    async def test_high_tier_catch_pays_plain_value_without_bonus_or_penalty(self):
        """O QTE foi removido: tier 3+ paga o valor cheio, sem x1.5 de acerto
        nem zeragem por erro/timeout."""
        conn = self._make_conn()
        user_id = 77

        ensure_user(conn, user_id, "Tester")
        add_inventory_item(conn, user_id, "isca", 2)

        ctx = {
            "user_id": user_id,
            "inv": {"isca": 1},
            "inv_before": {"isca": 2},
            "valor": 400,
            "nome": "Leviatã",
            "emoji": "🐉",
            "tier_p": 4,
            "frase": "Grande demais pro balde.",
            "rod_data": {"name": "Vara Teste", "luck": 1},
            "actual_cd": 300,
            "mission_msg": "",
            "mission_completed": False,
            "quest_trigger": False,
            "new_xp_total": 0,
            "current_rank": "F",
            "used_bait": True,
            "agora_str": "2026-08-13 12:05:00.000000",
            "w_key": "normal",
            "w_stats": {"name": "Normal", "luck_mod": 1},
            "is_trash": False,
        }

        interaction = self._make_interaction()
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia._finalize_pescar(interaction, ctx)

        # Exatamente o valor sorteado: nem 600 (x1.5) nem 0 (linha arrebentada).
        self.assertEqual(get_wallet(conn, user_id), 400)

    async def test_no_qte_entrypoints_remain(self):
        """Guarda contra o QTE voltar por engano."""
        for attr in ("TensionQTEView", "_finalize_pescar_timeout"):
            self.assertFalse(hasattr(economia, attr), f"{attr} deveria ter sido removido")


class ComprarTests(unittest.IsolatedAsyncioTestCase):
    """/eco comprar migrado pra v4: gasto atômico (try_spend_wallet) em vez
    do padrão antigo `if wallet < price` + `UPDATE ... SET wallet = ?` com
    valor absoluto calculado a partir de um snapshot.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_successful_purchase_deducts_wallet_and_adds_item(self):
        conn = self._make_conn()
        user_id = 10
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.comprar.callback(interaction, "isca")

        self.assertEqual(get_wallet(conn, user_id), 1000 - economia.SHOP_ITEMS["isca"]["price"])
        self.assertEqual(get_inventory(conn, user_id).get("isca"), 1)
        interaction.response.send_message.assert_called_once()
        self.assertIn("Compra realizada", interaction.response.send_message.call_args.args[0])

    async def test_insufficient_balance_refuses_without_changing_state(self):
        conn = self._make_conn()
        user_id = 11
        ensure_user(conn, user_id, "Pobre")
        modify_wallet(conn, user_id, 10, "Pobre")  # menos que o preço da isca (50)

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.comprar.callback(interaction, "isca")

        self.assertEqual(get_wallet(conn, user_id), 10)
        self.assertEqual(get_inventory(conn, user_id).get("isca", 0), 0)
        rejection = interaction.response.send_message.call_args.args[0]
        self.assertIn("Sem saldo", rejection)

    async def test_no_existing_account_is_refused_and_does_not_auto_create_one(self):
        conn = self._make_conn()
        user_id = 12

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.comprar.callback(interaction, "isca")

        rejection = interaction.response.send_message.call_args.args[0]
        self.assertIn("pescar primeiro", rejection)
        # try_spend_wallet nunca deveria ter rodado (e portanto nunca criado
        # conta) — comprar() não deve vivificar contas novas.
        row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        self.assertIsNone(row)


class ShopRodPurchaseTests(unittest.IsolatedAsyncioTestCase):
    """ROTA A de /eco loja (compra direta de vara pelo dropdown), migrada
    pra v4: gasto atômico em vez de `if wallet < custo` + escrita separada.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def _get_shop_select(self, conn, interaction):
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.loja.callback(interaction)
        view = interaction.followup.send.call_args.kwargs["view"]
        return view.children[0]

    async def test_rod_purchase_deducts_wallet_adds_item_and_equips(self):
        conn = self._make_conn()
        user_id = 20
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 5000, "Tester")

        select = await self._get_shop_select(conn, self._make_interaction(user_id))
        select._values = ["vara_treino"]

        buy_interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(buy_interaction)

        custo = economia.SHOP_ITEMS["vara_treino"]["price"]
        self.assertEqual(get_wallet(conn, user_id), 5000 - custo)
        self.assertEqual(get_inventory(conn, user_id).get("vara_treino"), 1)
        self.assertEqual(get_current_rod(conn, user_id), "vara_treino")
        buy_interaction.response.send_message.assert_called_once()
        self.assertIn("Compra Efetuada", buy_interaction.response.send_message.call_args.args[0])

    async def test_rod_purchase_refuses_without_writing_when_insufficient(self):
        conn = self._make_conn()
        user_id = 21
        ensure_user(conn, user_id, "Pobre")
        modify_wallet(conn, user_id, 10, "Pobre")

        select = await self._get_shop_select(conn, self._make_interaction(user_id))
        select._values = ["vara_treino"]

        buy_interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(buy_interaction)

        self.assertEqual(get_wallet(conn, user_id), 10)
        self.assertEqual(get_inventory(conn, user_id).get("vara_treino", 0), 0)
        self.assertEqual(get_current_rod(conn, user_id), "vara_bambu")
        self.assertIn("Falta grana", buy_interaction.response.send_message.call_args.args[0])


class CompraQuantidadeModalTests(unittest.IsolatedAsyncioTestCase):
    """Bug corrigido: saldo capturado quando o dropdown foi clicado e nunca
    relido, mesmo a modal ficando aberta um tempo arbitrário até o submit.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def _make_modal(self, item_key, conn, user_id, quantidade):
        modal = economia.CompraQuantidadeModal(
            item_key, economia.SHOP_ITEMS[item_key], user_id, SimpleNamespace(db_conn=conn)
        )
        modal.qtd._value = str(quantidade)
        return modal

    async def test_on_submit_uses_balance_fresh_at_submit_time(self):
        conn = self._make_conn()
        user_id = 30
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")

        modal = self._make_modal("isca", conn, user_id, 5)

        # Gasto externo depois que a modal já foi construída (ex.: outro
        # comando rodando enquanto o jogador ainda preenche a modal).
        modify_wallet(conn, user_id, -950)  # sobra 50

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await modal.on_submit(interaction)

        # Preço unitário da isca x5 exige mais que os 50 restantes — tem que
        # recusar usando o saldo ATUAL (50), não os 1000 de quando a modal
        # foi aberta.
        msg = interaction.response.send_message.call_args.args[0]
        self.assertIn("Saldo Insuficiente", msg)
        self.assertIn("50", msg)
        self.assertEqual(get_wallet(conn, user_id), 50)
        self.assertEqual(get_inventory(conn, user_id).get("isca", 0), 0)

    async def test_on_submit_succeeds_with_fresh_balance(self):
        conn = self._make_conn()
        user_id = 31
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")

        modal = self._make_modal("isca", conn, user_id, 3)

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await modal.on_submit(interaction)

        custo = economia.SHOP_ITEMS["isca"]["price"] * 3
        self.assertEqual(get_wallet(conn, user_id), 1000 - custo)
        self.assertEqual(get_inventory(conn, user_id).get("isca"), 3)
        self.assertIn("Compra Confirmada", interaction.response.send_message.call_args.args[0])


class PresentearTests(unittest.IsolatedAsyncioTestCase):
    """/eco presentear é transferência pura: exige posse, move o item do
    inventário do remetente para o do destinatário e nunca toca na carteira.
    Não compra o item para quem não o tem, e não equipa nada no destinatário
    (nem varas) — quem equipa é o próprio jogador, pelo menu do /eco saldo.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_flex_gift_transfers_owned_copy_without_touching_wallet(self):
        conn = self._make_conn()
        sender_id, receiver_id = 40, 41
        ensure_user(conn, sender_id, "Sender")
        modify_wallet(conn, sender_id, 6000, "Sender")
        add_inventory_item(conn, sender_id, "certificado", 1)

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "certificado")

        # Transferência: a cópia sai do remetente e a carteira não é tocada
        # (presentear não é mais uma compra disfarçada).
        self.assertEqual(get_wallet(conn, sender_id), 6000)
        self.assertEqual(get_inventory(conn, sender_id).get("certificado", 0), 0)
        # Entrega pela CHAVE interna, não pelo nome de exibição: gravar por
        # data['name'] criava uma segunda grafia do mesmo item no inventário.
        self.assertEqual(get_inventory(conn, receiver_id).get("certificado"), 1)
        self.assertEqual(get_inventory(conn, receiver_id).get("Certificado de Dono", 0), 0)
        self.assertIn("Enviado", interaction.response.send_message.call_args.args[0])

    async def test_gift_refused_when_sender_lacks_item_even_with_balance(self):
        conn = self._make_conn()
        sender_id, receiver_id = 42, 43
        ensure_user(conn, sender_id, "Rico")
        # Saldo de sobra para o preço de loja do item (5000) — irrelevante:
        # sem a cópia na mochila não há presente, e nada é comprado.
        modify_wallet(conn, sender_id, 999999, "Rico")

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "certificado")

        self.assertEqual(get_wallet(conn, sender_id), 999999)
        self.assertEqual(get_inventory(conn, receiver_id).get("certificado", 0), 0)
        self.assertIn("não possui esse item", interaction.response.send_message.call_args.args[0])

    async def test_rod_gift_not_blocked_by_receiver_rod_tier(self):
        conn = self._make_conn()
        sender_id, receiver_id = 44, 45
        ensure_user(conn, sender_id, "Sender")
        add_inventory_item(conn, sender_id, "vara_plastico", 1)
        ensure_user(conn, receiver_id, "Amigo")
        sync_user_to_economy(conn, receiver_id)
        conn.execute("UPDATE economy SET rod_tier = 5 WHERE user_id = ?", (receiver_id,))
        conn.commit()

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "vara_plastico")

        # Não há mais recusa por comparação de rod_tier: a vara é só um item
        # de inventário e o destinatário decide se equipa.
        self.assertNotIn("já tem vara melhor", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_inventory(conn, receiver_id).get("vara_plastico"), 1)
        self.assertEqual(get_inventory(conn, sender_id).get("vara_plastico", 0), 0)

    async def test_rod_gift_lands_in_inventory_without_auto_equipping(self):
        conn = self._make_conn()
        sender_id, receiver_id = 46, 47
        ensure_user(conn, sender_id, "Sender")
        add_inventory_item(conn, sender_id, "vara_plastico", 1)
        ensure_user(conn, receiver_id, "Amigo")

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "vara_plastico")

        # A vara entra na mochila (é daí que o menu de equipar monta owned_rods)
        # mas NÃO é equipada: trocar o equipamento em uso de outra pessoa sem
        # ela pedir tira a escolha dela e pode até rebaixar a vara ativa.
        self.assertEqual(get_inventory(conn, receiver_id).get("vara_plastico"), 1)
        self.assertEqual(get_current_rod(conn, receiver_id), "vara_bambu")

    async def test_free_gift_consumes_sender_owned_copy(self):
        conn = self._make_conn()
        # item_dono é gratuito (price=0) e só pode ser dado pelo ID_DONO
        # hardcoded em presentear() — usa esse ID de propósito aqui.
        sender_id = 541680099477422110
        receiver_id = 48
        ensure_user(conn, sender_id, "Dono")
        add_inventory_item(conn, sender_id, "item_dono", 1)

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "item_dono")

        self.assertEqual(get_inventory(conn, sender_id).get("item_dono", 0), 0)
        self.assertEqual(get_inventory(conn, receiver_id).get("item_dono"), 1)

    async def test_free_gift_refused_when_sender_does_not_own_it(self):
        conn = self._make_conn()
        sender_id = 541680099477422110
        receiver_id = 49
        ensure_user(conn, sender_id, "Dono")  # não possui item_dono

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "item_dono")

        self.assertIn("não possui esse item", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_inventory(conn, receiver_id).get("item_dono", 0), 0)


class ExplorarTests(unittest.IsolatedAsyncioTestCase):
    """/eco explorar migrado pra v4. O foco é a reserva de custo+cooldown
    ANTES do `await view.wait()` (até 60s esperando o jogador escolher
    Ilha/Cidade) — mesmo padrão de fix já aplicado em /eco pescar.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _make_city_spotted_user(self, conn, user_id, wallet=1000):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (user_id, "Tester", wallet),
        )
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'city_spotted')",
            (user_id,),
        )
        conn.commit()
        # Materializa a linha v4. Depois da etapa 3 o portão "tem conta?" de
        # /eco explorar lê `users`, não mais a legada — e todo jogador vivo
        # tem essa linha (migrate_to_normalized roda no boot, em main.py).
        ensure_user(conn, user_id, "Tester")

    async def test_second_explorar_during_open_view_window_is_rejected_by_cooldown(self):
        conn = self._make_conn()
        user_id = 500
        self._make_city_spotted_user(conn, user_id)

        interaction1 = self._make_interaction(user_id)
        interaction2 = self._make_interaction(user_id)

        # Força o sorteio da ROTA 2 (farm, disparada quando task1 resolver
        # com "farm") pra um resultado sem ganho — assim o saldo final é
        # previsível (só o custo do drone é debitado), sem depender de qual
        # cenário de recompensa o RNG sortear.
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "choices", return_value=[("💥 Falha Crítica", "Explodiu.", 0, None)]):
            task1 = asyncio.create_task(economia.explorar.callback(interaction1))
            await asyncio.sleep(0)
            self.assertFalse(task1.done(), "esperava a 1ª chamada suspensa aguardando a escolha de destino")

            # Custo e cooldown já reservados nesse ponto, mesmo com a 1ª
            # chamada ainda esperando o jogador escolher o destino.
            self.assertEqual(get_wallet(conn, user_id), 1000 - 80)
            self.assertIsNotNone(get_cooldowns(conn, user_id)["last_explore"])

            # 2ª chamada do mesmo usuário enquanto a 1ª ainda está aberta.
            await economia.explorar.callback(interaction2)

            # A 2ª chamada não pode ter cobrado de novo enquanto a 1ª ainda
            # está aberta.
            self.assertEqual(get_wallet(conn, user_id), 1000 - 80)

            # Resolve a 1ª chamada manualmente (sem esperar os 60s reais do
            # timeout da view).
            view = interaction1.response.send_message.call_args.kwargs["view"]
            view.choice = "farm"
            view.stop()
            await task1

        rejection = interaction2.response.send_message.call_args.args[0]
        self.assertIn("Drone Recarregando", rejection)
        # Cobrado só uma vez no total (pela 1ª chamada) — a rota de farm
        # sorteada não deu nem ganho nem item, só o custo original.
        self.assertEqual(get_wallet(conn, user_id), 1000 - 80)

    async def test_view_no_choice_still_consumes_cost_and_cooldown_and_notifies(self):
        conn = self._make_conn()
        user_id = 501
        self._make_city_spotted_user(conn, user_id)

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            task = asyncio.create_task(economia.explorar.callback(interaction))
            await asyncio.sleep(0)
            view = interaction.response.send_message.call_args.kwargs["view"]
            view.stop()  # simula timeout: nenhum destino foi escolhido
            await task

        self.assertEqual(get_wallet(conn, user_id), 1000 - 80)
        self.assertIsNotNone(get_cooldowns(conn, user_id)["last_explore"])
        msg = interaction.followup.send.call_args.args[0]
        self.assertIn("perdeu o sinal", msg)

    async def test_farm_route_insufficient_wallet_refuses(self):
        conn = self._make_conn()
        user_id = 502
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)", (user_id, "Pobre", 10)
        )
        conn.commit()
        # Mesmo motivo de _make_city_spotted_user: o portão "tem conta?" lê a
        # v4 desde a etapa 3, então a conta precisa existir lá.
        ensure_user(conn, user_id, "Pobre")

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.explorar.callback(interaction)

        self.assertEqual(get_wallet(conn, user_id), 10)
        self.assertIsNone(get_cooldowns(conn, user_id)["last_explore"])
        self.assertIn("Precisa de", interaction.response.send_message.call_args.args[0])

    async def test_farm_route_cooldown_refuses(self):
        conn = self._make_conn()
        user_id = 503
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")
        sync_user_to_economy(conn, user_id)
        set_cooldown(conn, user_id, "last_explore", datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"))

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.explorar.callback(interaction)

        self.assertEqual(get_wallet(conn, user_id), 1000)  # não cobrou de novo
        self.assertIn("Drone Recarregando", interaction.response.send_message.call_args.args[0])

    async def test_farm_route_energetico_outcome_resets_fish_cooldown(self):
        conn = self._make_conn()
        user_id = 504
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")
        set_cooldown(conn, user_id, "last_fish", "2026-08-13 12:00:00.000000")
        sync_user_to_economy(conn, user_id)

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "choices", return_value=[("⚡ Energético Perdido", "Achou uma latinha.", 0, "energetico")]):
            await economia.explorar.callback(interaction)

        self.assertIsNone(get_cooldowns(conn, user_id)["last_fish"])
        self.assertEqual(get_wallet(conn, user_id), 1000 - 80)  # custo do drone continua cobrado

    async def test_acesso_negado_refunds_cooldown_but_not_cost(self):
        conn = self._make_conn()
        user_id = 505
        self._make_city_spotted_user(conn, user_id)
        # Sem selo_capitao no inventário da quest -> acesso negado.

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            task = asyncio.create_task(economia.explorar.callback(interaction))
            await asyncio.sleep(0)
            view = interaction.response.send_message.call_args.kwargs["view"]
            view.choice = "city"
            view.stop()
            await task

        # Custo do drone continua cobrado (comportamento original preservado).
        self.assertEqual(get_wallet(conn, user_id), 1000 - 80)
        # Cooldown reembolsado — pode tentar de novo sem esperar 10min.
        self.assertIsNone(get_cooldowns(conn, user_id)["last_explore"])
        embed = interaction.followup.send.call_args.kwargs["embed"]
        self.assertIn("ACESSO NEGADO", embed.title)


class GaldinoTuneBtnTests(unittest.IsolatedAsyncioTestCase):
    """GaldinoView.tune_btn migrado pra v4: sucata/upgrades capturados na
    abertura da view (que fica aberta até 180s) nunca são reutilizados nos
    cliques — try_upgrade_rod relê e recalcula o custo a partir do nível
    fresco a cada clique.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def _get_button(self, view, label):
        for child in view.children:
            if child.label == label:
                return child
        raise AssertionError(f"botão {label!r} não encontrado")

    async def _open_tuning(self, conn, user_id):
        galdino_view = economia.GaldinoView(user_id, "Tester")
        tune_button = self._get_button(galdino_view, "Tunar Vara")
        open_interaction = self._make_interaction()
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await tune_button.callback(open_interaction)
        tuning_view = open_interaction.response.send_message.call_args.kwargs["view"]
        return self._get_button(tuning_view, "Upar Sorte"), self._get_button(tuning_view, "Upar CD")

    async def test_successive_clicks_recompute_cost_from_fresh_level_not_view_open_time(self):
        conn = self._make_conn()
        user_id = 60
        ensure_user(conn, user_id, "Tester")
        modify_scrap(conn, user_id, 1000)

        upar_sorte, _ = await self._open_tuning(conn, user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            click1 = self._make_interaction()
            await upar_sorte.callback(click1)
            click2 = self._make_interaction()
            await upar_sorte.callback(click2)

        self.assertIn("Sorte aumentada", click1.response.send_message.call_args.args[0])
        self.assertIn("Sorte aumentada", click2.response.send_message.call_args.args[0])
        self.assertEqual(get_rod_upgrades(conn, user_id)["luck"], 2)
        # 1º clique custou 100 (nível 0->1), 2º custou 200 (nível 1->2) —
        # recalculado do nível fresco, não do custo mostrado quando a view
        # abriu (que ainda dizia "Custo: 100" pros dois cliques).
        self.assertEqual(get_scrap(conn, user_id), 1000 - 100 - 200)

        # Sucata gasta e nível ganho continuam valendo depois de um comando
        # v4 seguinte — trava contra uma regressão do "escreve só na legada".
        modify_wallet(conn, user_id, 1)
        self.assertEqual(get_rod_upgrades(conn, user_id)["luck"], 2)
        self.assertEqual(get_scrap(conn, user_id), 1000 - 100 - 200)

    async def test_insufficient_scrap_refuses_without_changing_state(self):
        conn = self._make_conn()
        user_id = 61
        ensure_user(conn, user_id, "Tester")
        modify_scrap(conn, user_id, 50)

        upar_sorte, _ = await self._open_tuning(conn, user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            click = self._make_interaction()
            await upar_sorte.callback(click)

        self.assertIn("Sucata insuficiente", click.response.send_message.call_args.args[0])
        self.assertEqual(get_scrap(conn, user_id), 50)
        self.assertEqual(get_rod_upgrades(conn, user_id)["luck"], 0)

    async def test_max_level_refuses_after_five_upgrades(self):
        conn = self._make_conn()
        user_id = 62
        ensure_user(conn, user_id, "Tester")
        modify_scrap(conn, user_id, 100000)

        upar_cd, _ = None, None
        _, upar_cd = await self._open_tuning(conn, user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            for _ in range(5):
                click = self._make_interaction()
                await upar_cd.callback(click)
            final_click = self._make_interaction()
            await upar_cd.callback(final_click)

        self.assertIn("Max Level", final_click.response.send_message.call_args.args[0])
        self.assertEqual(get_rod_upgrades(conn, user_id)["cd"], 5)


class EnergeticoFixTests(unittest.IsolatedAsyncioTestCase):
    """Energético: preço recalculado (150 -> 900) e bug de coluna corrigido
    (escrevia em 'last_fish_time', uma coluna órfã nunca lida pela checagem
    real de cooldown, que usa 'last_fish' — o item não fazia nada).
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def test_price_is_900(self):
        self.assertEqual(economia.SHOP_ITEMS["energetico"]["price"], 900)

    async def test_using_energetico_resets_the_real_cooldown_column(self):
        conn = self._make_conn()
        user_id = 70
        ensure_user(conn, user_id, "Tester")
        add_inventory_item(conn, user_id, "energetico", 1)
        set_cooldown(conn, user_id, "last_fish", "2026-08-13 12:00:00.000000")

        select = economia.ConsumeSelect(user_id, {"energetico": 1})
        select._values = ["energetico"]
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)

        # A checagem real de cooldown em /eco pescar lê 'last_fish' — é essa
        # coluna que precisa ter sido zerada, não a órfã 'last_fish_time'
        # (nem presente no schema de teste — só existe em produção como
        # coluna morta, nunca lida por ninguém).
        self.assertIsNone(get_cooldowns(conn, user_id)["last_fish"])
        legacy = conn.execute(
            "SELECT last_fish FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertIsNone(legacy["last_fish"])
        self.assertIn("Energético bebido", interaction.response.send_message.call_args.args[0])

        # O item TEM que ter saído da mochila. Este teste só olhava cooldown e
        # mensagem — foi essa lacuna que deixou o bug do "item usado que volta"
        # passar batido pela suíte inteira antes das etapas 1-3.
        self.assertNotIn("energetico", get_inventory(conn, user_id))
        # E precisa continuar fora depois de um comando v4 qualquer, que é
        # quando o sync legada->v4 ressuscitava o item.
        modify_wallet(conn, user_id, 1)
        self.assertNotIn("energetico", get_inventory(conn, user_id))


class IscaEletricaDiscontinuedTests(unittest.TestCase):
    """isca_eletrica descontinuada (decisão: opção 2) — mesmo efeito do
    chip_sorte por 1/15 do preço. Removida da rotação da loja, mas continua
    em SHOP_ITEMS pra quem já possui cópias.
    """

    def test_never_appears_in_daily_shop_rotation(self):
        shop_keys = [item["key"] for item in economia.get_daily_shop()]
        self.assertNotIn("isca_eletrica", shop_keys)

    def test_still_defined_for_existing_owners(self):
        self.assertIn("isca_eletrica", economia.SHOP_ITEMS)


class DiarioStreakCapTests(unittest.IsolatedAsyncioTestCase):
    """/eco diario: bônus de streak capado em 60 dias (máx. +3.000) — sem
    teto, uma streak de 365 dias daria +18.250, ~90x a recompensa-base
    média (~200).
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_bonus_capped_at_60_days_even_with_longer_streak(self):
        conn = self._make_conn()
        user_id = 80
        ensure_user(conn, user_id, "Tester")
        # Streak de 90 dias já acumulada, último diário ontem (mantém streak).
        yesterday = datetime.now() - timedelta(days=1)
        conn.execute(
            "UPDATE user_cooldowns SET last_daily = ?, daily_streak = ? WHERE user_id = ?",
            (yesterday.strftime("%Y-%m-%d %H:%M:%S.%f"), 90, user_id),
        )
        sync_user_to_economy(conn, user_id)
        conn.commit()

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "randint", return_value=200):
            await economia.diario.callback(interaction)

        msg = interaction.response.send_message.call_args.args[0]
        # A streak em si (exibida ao jogador) continua sem teto: 90 -> 91.
        self.assertIn("dia 91", msg)
        # O bônus monetário é capado em 60 dias * 50 = 3.000, não 91*50=4.550.
        self.assertIn("+3000", msg)
        self.assertEqual(get_wallet(conn, user_id), 200 + 3000)

    async def test_bonus_not_capped_below_threshold(self):
        conn = self._make_conn()
        user_id = 81
        ensure_user(conn, user_id, "Tester")

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "randint", return_value=200):
            await economia.diario.callback(interaction)

        msg = interaction.response.send_message.call_args.args[0]
        self.assertIn("dia 1", msg)
        self.assertIn("+50", msg)
        self.assertEqual(get_wallet(conn, user_id), 200 + 50)


class PescarCooldownReservationTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /eco pescar disparado duas vezes em sequência rápida pelo
    mesmo usuário não pode abrir dois fluxos de captura em paralelo — a
    segunda chamada deve ser barrada pela checagem normal de cooldown.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))),
        )

    async def test_second_pescar_before_first_finishes_is_rejected_by_cooldown(self):
        """Reproduz o cenário real: a 1ª chamada engata um peixe e fica
        suspensa num await ANTES de `_finalize_pescar` gravar. Enquanto ela
        está pendurada aí, a 2ª chamada do mesmo usuário deve ser rejeitada
        pela checagem de cooldown, porque o cooldown já foi reservado no
        INÍCIO da 1ª chamada, e não apenas no final.

        O ponto de suspensão original era o `asyncio.sleep(2)` do QTE. Com o
        QTE removido, a suspensão é forçada dentro do próprio
        `_finalize_pescar` — o que a propriedade exige é justamente que a
        reserva já tenha acontecido antes dele, independente de qual await
        segure a chamada.
        """
        conn = self._make_conn()
        user_id = 999

        # Firewall zera a chance de lixo, garantindo que a 1ª chamada sempre
        # sorteie um peixe real. Inserida direto na tabela legada pra simular
        # um usuário que já existia antes desta migração — pescar() precisa
        # sincronizar isso pra v4 sozinho antes de ler.
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, current_rod, inventory) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 1000, "vara_void", json.dumps({"firewall": 1})),
        )
        conn.commit()

        interaction1 = self._make_interaction(user_id)
        interaction2 = self._make_interaction(user_id)

        finalize_entered = asyncio.Event()
        release_finalize = asyncio.Event()

        async def hanging_finalize(_interaction, _ctx):
            # Prende a 1ª pescaria exatamente no ponto onde ela gravaria o
            # resultado, deixando a 2ª chamada interlear enquanto isso.
            finalize_entered.set()
            await release_finalize.wait()

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia, "_finalize_pescar", side_effect=hanging_finalize):
            task1 = asyncio.create_task(economia.pescar.callback(interaction1))
            await asyncio.wait_for(finalize_entered.wait(), timeout=5)

            self.assertFalse(task1.done(), "esperava a 1ª chamada suspensa antes de gravar")

            # 2ª chamada do MESMO usuário enquanto a 1ª ainda não gravou.
            # O wait_for não é decorativo: sem a reserva de cooldown, esta
            # chamada NÃO é rejeitada, entra no fluxo de captura e trava no
            # mesmo `_finalize_pescar` suspenso — sem timeout, a regressão se
            # manifestaria como um teste pendurado para sempre em vez de uma
            # falha legível.
            try:
                await asyncio.wait_for(economia.pescar.callback(interaction2), timeout=5)
            except asyncio.TimeoutError:
                release_finalize.set()
                await task1
                self.fail(
                    "a 2ª pescaria não foi barrada pelo cooldown e abriu um "
                    "segundo fluxo de captura em paralelo — a reserva de "
                    "cooldown no início de pescar() foi perdida"
                )

            release_finalize.set()
            await task1

        # 2ª chamada: rejeitada pela checagem normal de cooldown — não abriu
        # um segundo fluxo de captura em paralelo.
        self.assertTrue(interaction2.followup.send.called)
        call2_args, call2_kwargs = interaction2.followup.send.call_args
        rejection_text = call2_args[0] if call2_args else call2_kwargs.get("content", "")
        self.assertIn("Descansando", rejection_text)
        self.assertNotIn("embed", call2_kwargs)

        # O cooldown foi reservado pela 1ª chamada (last_fish já gravado),
        # mesmo com a pescaria dela ainda não finalizada (fish_count == 0,
        # pois _finalize_pescar foi interceptado). Checa tanto a v4 quanto a
        # tabela legada (têm que estar em sincronia).
        row = conn.execute(
            "SELECT fish_count, last_fish FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertIsNotNone(row["last_fish"])
        self.assertEqual(row["fish_count"], 0)


class NewAccountPescarFlowTests(unittest.IsolatedAsyncioTestCase):
    """Fase 8: colapsa a criação de conta — a primeira chamada de
    /eco pescar de um usuário nunca visto antes não pode mais parar em
    '🆕 Conta criada! Tente pescar novamente.' Precisa criar a conta E
    entregar o resultado da primeira pescaria na MESMA chamada.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Novato"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_first_ever_pescar_call_creates_account_and_delivers_catch(self):
        conn = self._make_conn()
        user_id = 4242
        interaction = self._make_interaction(user_id)

        # Vara padrão de conta nova (vara_bambu, tier 0) só sorteia peixes
        # tier 0 — força "Sardinha" (peixe de verdade, fora de TRASH_ITEMS)
        # em vez de deixar ao acaso, pra tornar o resultado determinístico
        # sem precisar tocar em nenhuma outra regra de sorteio/valor.
        real_choice = economia.random.choice

        def choose_sardinha(seq):
            for item in seq:
                if isinstance(item, tuple) and len(item) == 6 and item[0] == "Sardinha":
                    return item
            return real_choice(seq)

        # Pré-condição: usuário realmente não existe em nenhuma tabela ainda.
        self.assertIsNone(conn.execute("SELECT 1 FROM economy WHERE user_id = ?", (user_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone())

        # A vara inicial tem 90% de chance de lixo, e nesse ramo o pool nem
        # contem Sardinha - forcar so o random.choice deixava o resultado
        # depender de sorte (o teste falhava de forma intermitente). A
        # rolagem de lixo, randint(1, 100), tambem precisa ser fixada acima
        # de trash_chance.
        _real_randint = economia.random.randint

        def _sem_lixo(a, b):
            return 100 if (a, b) == (1, 100) else _real_randint(a, b)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "randint", side_effect=_sem_lixo), \
             patch.object(economia.random, "choice", side_effect=choose_sardinha):
            await economia.pescar.callback(interaction)

        # A conta foi materializada na mesma chamada (users e economy).
        self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT 1 FROM economy WHERE user_id = ?", (user_id,)).fetchone())

        # Só UMA resposta foi enviada, e é o resultado da pescaria (embed
        # com o peixe capturado) — não a antiga mensagem de texto "conta
        # criada, tente de novo".
        interaction.followup.send.assert_awaited_once()
        _, kwargs = interaction.followup.send.call_args
        embed = kwargs.get("embed")
        self.assertIsNotNone(embed, "esperava o embed de resultado da pescaria na 1ª chamada")
        self.assertIn("Sardinha", embed.fields[0].value)

        # A pescaria de verdade rodou (cooldown reservado), não só a criação
        # de conta.
        from economy_db import get_cooldowns

        cd = get_cooldowns(conn, user_id)
        self.assertIsNotNone(cd["last_fish"])


class RodSelectEquipTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: jogador reportou não conseguir trocar de vara depois de
    comprar uma nova. Causa raiz: RodSelect.callback só escrevia
    current_rod na tabela legada `economy`, nunca em user_rods (v4) — e
    ensure_user() só sincroniza legada->v4 nessa tabela com INSERT OR
    IGNORE (só na criação da conta), então a troca "funcionava" (mensagem
    de sucesso) mas /eco pescar continuava lendo a vara antiga da v4.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _make_rod_select(self, user_id, owned_rods, current_rod_key, chosen_rod):
        from cogs.economia import RodSelect

        select = RodSelect(user_id, owned_rods, current_rod_key)
        select._values = [chosen_rod]
        return select

    async def test_equipping_a_purchased_rod_updates_the_v4_table_pescar_actually_reads(self):
        from economy_db import ensure_user, get_current_rod

        conn = self._make_conn()
        user_id = 5001
        # Usuário já existente com vara_bambu equipada e vara_ouro comprada
        # (já no inventário) — cenário exato do bug reportado.
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, current_rod, inventory) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 5000, "vara_bambu", json.dumps({"vara_ouro": 1})),
        )
        conn.commit()
        # Materializa user_rods (v4) ANTES da troca, simulando um jogador
        # que já pescou antes — crítico para o teste ser fiel ao bug real:
        # sync_user_from_economy só faz INSERT OR IGNORE em user_rods, ou
        # seja, só sincroniza da legada pra v4 na CRIAÇÃO da linha. Se a
        # linha não existisse ainda neste ponto, a própria chamada de
        # ensure_user() dentro de pescar() faria essa sincronização de
        # graça na 1ª vez, mascarando o bug em vez de reproduzi-lo.
        ensure_user(conn, user_id, "Tester")
        self.assertEqual(get_current_rod(conn, user_id), "vara_bambu")

        select = self._make_rod_select(user_id, ["vara_bambu", "vara_ouro"], "vara_bambu", "vara_ouro")
        interaction = self._make_interaction(user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)

        # Antes do fix, isto ficava 'vara_bambu' (nunca sincronizado) mesmo
        # com a mensagem de sucesso já tendo sido enviada.
        self.assertEqual(get_current_rod(conn, user_id), "vara_ouro")
        interaction.response.send_message.assert_awaited_once()

    async def test_pescar_after_equipping_actually_uses_the_new_rod(self):
        """Reprodução fim-a-fim do sintoma relatado: troca de vara, depois
        pesca de verdade — o resultado precisa refletir a vara NOVA."""
        from economy_db import ensure_user

        conn = self._make_conn()
        user_id = 5002
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, current_rod, inventory) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 5000, "vara_bambu", json.dumps({"vara_ouro": 1, "firewall": 1})),
        )
        conn.commit()
        # Mesmo motivo do teste acima: materializa user_rods ANTES da troca,
        # simulando um jogador que já pescou antes (senão o bug fica
        # mascarado pelo INSERT OR IGNORE de 1ª sincronização).
        ensure_user(conn, user_id, "Tester")

        select = self._make_rod_select(user_id, ["vara_bambu", "vara_ouro"], "vara_bambu", "vara_ouro")
        equip_interaction = self._make_interaction(user_id)

        real_choice = economia.random.choice

        def choose_low_tier(seq):
            candidates = [item for item in seq if isinstance(item, tuple) and len(item) == 6 and 0 < item[4] <= 2]
            return candidates[0] if candidates else real_choice(seq)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(equip_interaction)

            pescar_interaction = self._make_interaction(user_id)
            with patch.object(economia.random, "choice", side_effect=choose_low_tier):
                await economia.pescar.callback(pescar_interaction)

        pescar_interaction.followup.send.assert_awaited_once()
        _, kwargs = pescar_interaction.followup.send.call_args
        embed = kwargs.get("embed")
        self.assertIsNotNone(embed)
        detalhes_field = next(f for f in embed.fields if f.name == "Detalhes")
        self.assertIn(
            "Vara de Ouro",
            detalhes_field.value,
            "pescar() usou a vara antiga (vara_bambu) mesmo depois da troca — bug reportado não corrigido",
        )


class TrapAutoTransitionTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: quando o timer da armadilha vencia, as transições
    automáticas (working->ready e cooldown->idle) faziam
    `await self.trap_manager(interaction, button)`. Mas @discord.ui.button
    troca esse atributo por um discord.ui.Button na instância da View, que
    não é chamável — todo jogador que voltasse na máquina depois do timer
    vencer tomava TypeError em vez do painel.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _make_view(self, user_id):
        view = economia.GaldinoView(user_id, "Tester")
        # O atributo continua sendo um Button (não chamável): é exatamente
        # essa a premissa do bug, então o teste avisaria caso alguém
        # "consertasse" removendo o decorator em vez de corrigir a chamada.
        self.assertIsInstance(view.trap_manager, discord.ui.Button)
        self.assertFalse(callable(view.trap_manager))
        return view

    async def _click(self, view, interaction):
        """Dispara pelo mesmo caminho que o discord.py usa (Button.callback),
        não pelo método interno — senão o teste não passa pelo atributo que
        quebrava."""
        await view.trap_manager.callback(interaction)

    def _seed(self, conn, user_id):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (user_id, "Tester", 500),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    async def test_expired_working_timer_renders_ready_panel_without_crashing(self):
        conn = self._make_conn()
        user_id = 7101
        self._seed(conn, user_id)
        # Timer já vencido: é o estado que dispara a transição automática.
        set_trap(conn, user_id, {"type": "covo_basico", "status": "working", "timer_end": 1})

        view = self._make_view(user_id)
        interaction = self._make_interaction(user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await self._click(view, interaction)

        # Antes do fix isto estourava TypeError: 'Button' object is not callable.
        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        self.assertIn("READY", kwargs["embed"].title)
        self.assertEqual(get_trap(conn, user_id)["status"], "ready")

    async def test_expired_cooldown_timer_renders_idle_panel_without_crashing(self):
        conn = self._make_conn()
        user_id = 7102
        self._seed(conn, user_id)
        set_trap(conn, user_id, {"type": "covo_basico", "status": "cooldown", "timer_end": 1})

        view = self._make_view(user_id)
        interaction = self._make_interaction(user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await self._click(view, interaction)

        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        self.assertIn("IDLE", kwargs["embed"].title)
        self.assertEqual(get_trap(conn, user_id)["status"], "idle")

    async def test_unexpired_working_timer_still_shows_the_waiting_panel(self):
        """Contraprova: sem timer vencido não há transição, então o painel
        continua sendo o de espera (o fix não mexeu nesse caminho)."""
        conn = self._make_conn()
        user_id = 7103
        self._seed(conn, user_id)
        future = datetime.now().timestamp() + 600
        set_trap(conn, user_id, {"type": "covo_basico", "status": "working", "timer_end": future})

        view = self._make_view(user_id)
        interaction = self._make_interaction(user_id)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await self._click(view, interaction)

        _, kwargs = interaction.response.send_message.call_args
        self.assertIn("WORKING", kwargs["embed"].title)
        self.assertEqual(get_trap(conn, user_id)["status"], "working")


class JennaPromotionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: a promoção da Capitã Jenna fazia
    `UPDATE economy SET guild_rank, guild_xp`, ou seja, escrevia só na tabela
    legada, deixando `users` (v4) desatualizado.

    Atenção ao mecanismo exato: guild_rank/guild_xp NÃO são revertidos por um
    comando v4 qualquer, porque sync_user_from_economy() faz upsert dessas
    duas colunas de volta para users (diferente de user_trap/user_rods, que
    usam INSERT OR IGNORE). O que reverte é a janela entre a promoção e o
    próximo ensure_user(): os caminhos de recompensa de missão em grupo
    (economia.py:1049 e :1325) fazem `UPDATE users SET guild_xp = guild_xp+?`
    seguido de sync_user_to_economy() SEM ensure_user antes — então leem o
    users obsoleto e reescrevem economy a partir dele, apagando a promoção.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def _ask_promo(self, conn, user_id):
        """Abre o menu real da Jenna e seleciona 'Pedir Promoção'.

        JennaSelect é uma classe local dentro de talk_jenna, então o único
        jeito fiel de exercitá-la é pegar a view que o botão envia.
        """
        view = economia.GuildView(user_id, "Tester")
        open_interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await view.talk_jenna.callback(open_interaction)

            _, kwargs = open_interaction.response.send_message.call_args
            select = kwargs["view"].children[0]
            select._values = ["ask_promo"]

            promo_interaction = self._make_interaction(user_id)
            await select.callback(promo_interaction)
        return promo_interaction

    async def test_promotion_survives_the_next_v4_command(self):
        conn = self._make_conn()
        user_id = 7201
        # Rank E com 1600 XP: sair de E custa o req_xp de D (1500), então a
        # promoção é elegível e sobram 100 XP. Rank F não serve aqui porque
        # subir dele custa só 500 e a margem ficaria estreita demais para
        # separar "promoveu" de "promoveu com o limiar errado".
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, guild_rank, guild_xp) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 100, "E", 1600),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

        promo_interaction = await self._ask_promo(conn, user_id)

        promo_interaction.response.edit_message.assert_awaited_once()
        _, kwargs = promo_interaction.response.edit_message.call_args
        self.assertIn("Rank D", kwargs["embed"].description)
        # Lê users direto, sem get_guild_rank(): ele chama ensure_user ->
        # sync_user_from_economy, que consertaria a dessincronia e mascararia
        # o bug antes da etapa de reprodução abaixo.
        v4 = conn.execute(
            "SELECT guild_rank, guild_xp FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual((v4["guild_rank"], v4["guild_xp"]), ("D", 100))

        # Recompensa de missão em grupo, exatamente como economia.py:1049-1050:
        # escreve users direto e sincroniza para economy sem passar por
        # ensure_user. Pré-fix, users ainda dizia E/600 aqui e este sync
        # devolvia o jogador para o rank E.
        conn.execute(
            "UPDATE users SET guild_xp = guild_xp + ? WHERE user_id = ?", (50, user_id)
        )
        sync_user_to_economy(conn, user_id)

        self.assertEqual(get_guild_rank(conn, user_id)["rank"], "D")
        legacy = conn.execute(
            "SELECT guild_rank, guild_xp FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual(
            legacy["guild_rank"],
            "D",
            "promoção revertida pelo sync do comando seguinte — bug original",
        )
        # 100 de sobra da promoção + 50 da recompensa; pré-fix dava 650.
        self.assertEqual(legacy["guild_xp"], 150)

    async def test_promotion_refused_without_enough_xp_leaves_rank_untouched(self):
        conn = self._make_conn()
        user_id = 7202
        # Rank E exige 500 XP; com 10 a promoção tem que ser recusada.
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, guild_rank, guild_xp) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 100, "E", 10),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

        promo_interaction = await self._ask_promo(conn, user_id)

        _, kwargs = promo_interaction.response.edit_message.call_args
        self.assertIn("XP suficiente", kwargs["embed"].description)
        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "E", "xp": 10})


class GuildRankHelperTests(unittest.TestCase):
    """Helpers v4 de rank/XP: mesmo contrato de set_trap/set_inventory_item
    (grava na v4 e propaga para a legada no mesmo commit)."""

    def test_set_guild_rank_writes_v4_and_propagates_to_legacy(self):
        conn = _make_pescar_conn()
        user_id = 7301
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, ?, ?)",
            (user_id, "Tester", "F", 0),
        )
        conn.commit()

        set_guild_rank(conn, user_id, "D", 250)

        users_row = conn.execute(
            "SELECT guild_rank, guild_xp FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual((users_row["guild_rank"], users_row["guild_xp"]), ("D", 250))
        legacy = conn.execute(
            "SELECT guild_rank, guild_xp FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual((legacy["guild_rank"], legacy["guild_xp"]), ("D", 250))

    def test_get_guild_rank_defaults_and_normalizes(self):
        conn = _make_pescar_conn()
        user_id = 7302
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, ?, ?)",
            (user_id, "Tester", None, "42"),
        )
        conn.commit()

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "F", "xp": 42})

    def test_set_guild_rank_clamps_negative_xp(self):
        conn = _make_pescar_conn()
        user_id = 7303
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, ?, ?)",
            (user_id, "Tester", "F", 0),
        )
        conn.commit()

        set_guild_rank(conn, user_id, "E", -5)

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "E", "xp": 0})



class GuildXpStalePropagationTests(unittest.IsolatedAsyncioTestCase):
    """Etapa 1c: escrita crua em `users` a partir de estado defasado.

    Achado real desta etapa: os dois pontos citados no ticket
    (economia.py:1051 e :1327) já estavam protegidos por acaso — o
    `modify_wallet` da linha anterior chama ensure_user. O ponto que
    realmente perdia dado era _finalize_pescar, que grava
    `guild_xp`/`guild_rank` ABSOLUTOS a partir do snapshot lido no início de
    pescar() — snapshot esse que é anterior à recompensa de missão de grupo
    concedida no meio do mesmo comando.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _seed(self, conn, user_id, rank, xp):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, guild_rank, guild_xp) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 0, rank, xp),
        )
        # Capítulo que habilita XP de guilda na pescaria.
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    def _arm_group_mission(self, conn, user_id):
        """Party de um membro só com a missão f1 (fish_count, alvo 5, 40 XP)
        a uma pescaria de fechar — a próxima captura paga a recompensa."""
        conn.execute(
            "INSERT INTO parties (leader_id, leader_name, members_json, active_mission_id, mission_progress, mission_target) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "Tester", json.dumps([]), "f1", 4, 5),
        )
        conn.commit()

    async def _pescar(self, conn, user_id):
        """Pesca forçando um peixe de tier baixo (XP de guilda = 2), para o
        número esperado ser determinístico."""
        interaction = self._make_interaction(user_id)
        real_choice = economia.random.choice

        def low_tier(seq):
            cands = [i for i in seq if isinstance(i, tuple) and len(i) == 6 and 0 < i[4] <= 1]
            return cands[0] if cands else real_choice(seq)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.random, "choice", side_effect=low_tier):
            await economia.pescar.callback(interaction)
        return interaction

    async def test_group_mission_reward_is_not_clobbered_by_pescar_finalize(self):
        conn = self._make_conn()
        user_id = 7401
        self._seed(conn, user_id, "E", 100)
        self._arm_group_mission(conn, user_id)

        await self._pescar(conn, user_id)

        # 100 iniciais + 40 da missão de grupo + 2 da pescaria (tier baixo).
        # Pré-fix isto dava 102: _finalize_pescar gravava 100+2 absoluto,
        # apagando os 40 concedidos em economia.py:1051 no mesmo comando.
        v4 = conn.execute(
            "SELECT guild_xp FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual(
            v4["guild_xp"],
            142,
            "XP da missão de grupo foi sobrescrito pelo snapshot de pescar()",
        )
        legacy = conn.execute(
            "SELECT guild_xp FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual(legacy["guild_xp"], 142)

    async def test_promotion_then_group_reward_keeps_promoted_rank(self):
        """Cenário exato pedido no ticket: promoção E/1600 -> D/100, seguida de
        recompensa de missão de grupo. Rank tem que continuar D e o XP tem que
        somar, nunca voltar para o estado pré-promoção.

        O XP semeado era 600 quando a promoção comparava com o `req_xp` do rank
        ATUAL (E, 500). Com o limiar correto — o `req_xp` do PRÓXIMO rank (D,
        1500) — o mesmo cenário precisa de 1600 para sobrar os mesmos 100. O que
        o teste verifica (a promoção sobreviver ao sync seguinte) não mudou.
        """
        conn = self._make_conn()
        user_id = 7402
        self._seed(conn, user_id, "E", 1600)

        # Promoção pelo fluxo real da Jenna (mesmo caminho da etapa 1b).
        view = economia.GuildView(user_id, "Tester")
        open_interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await view.talk_jenna.callback(open_interaction)
            _, kwargs = open_interaction.response.send_message.call_args
            select = kwargs["view"].children[0]
            select._values = ["ask_promo"]
            await select.callback(self._make_interaction(user_id))

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "D", "xp": 100})

        # Recompensa de missão de grupo logo depois.
        self._arm_group_mission(conn, user_id)
        await self._pescar(conn, user_id)

        # Sair de D custa o req_xp de C (4000), então 142 não promove de novo.
        self.assertEqual(
            get_guild_rank(conn, user_id),
            {"rank": "D", "xp": 142},
            "rank/XP reverteram para o estado anterior à promoção",
        )


class AddGuildXpHelperTests(unittest.TestCase):
    """add_guild_xp: relê o XP dentro da transação, em vez de somar sobre um
    `users` que pode estar defasado em relação à legada."""

    def _seed(self, conn, user_id, xp):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, ?, ?)",
            (user_id, "Tester", "E", xp),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    def test_add_guild_xp_accumulates_on_the_current_v4_value(self):
        """O incremento sai do valor que está em `users` na hora da transação,
        não de um valor capturado antes — um escritor v4 no meio do caminho
        não pode ser sobrescrito.

        Nota (etapa 2): até a etapa 1c este teste partia de um `users`
        defasado em relação à legada, apostando no ensure_user interno para
        reconciliar. Depois que a etapa 2 tirou a reimportação do
        ensure_user, esse estado deixou de ser recuperável — e deixou de ser
        alcançável, já que nenhum fluxo escreve só na legada. O que o helper
        garante hoje é a releitura dentro da transação, que é o que se testa.
        """
        conn = _make_pescar_conn()
        user_id = 7501
        self._seed(conn, user_id, 100)

        # Outro escritor v4 avança o XP depois do seed.
        set_guild_rank(conn, user_id, "E", 500)

        novo = add_guild_xp(conn, user_id, 50)

        self.assertEqual(novo, 550)
        self.assertEqual(get_guild_rank(conn, user_id)["xp"], 550)
        legacy = conn.execute(
            "SELECT guild_xp FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertEqual(legacy["guild_xp"], 550)

    def test_add_guild_xp_is_cumulative_across_calls(self):
        conn = _make_pescar_conn()
        user_id = 7503
        self._seed(conn, user_id, 100)

        self.assertEqual(add_guild_xp(conn, user_id, 40), 140)
        self.assertEqual(add_guild_xp(conn, user_id, 40), 180)
        self.assertEqual(get_guild_rank(conn, user_id)["xp"], 180)

    def test_add_guild_xp_clamps_at_zero(self):
        conn = _make_pescar_conn()
        user_id = 7502
        self._seed(conn, user_id, 30)

        self.assertEqual(add_guild_xp(conn, user_id, -100), 0)
        self.assertEqual(get_guild_rank(conn, user_id)["xp"], 0)



class LegacyResurrectionTests(unittest.IsolatedAsyncioTestCase):
    """Etapa 2 — a raiz do sync assimétrico.

    sync_user_from_economy() faz upsert do que a legada TEM mas nunca apaga o
    que sumiu de lá; sync_user_to_economy() reescreve a legada inteira a
    partir da v4. Enquanto ensure_user() chamava o primeiro em toda chamada —
    e quase todo helper v4 começa por ensure_user — qualquer coisa já gasta
    ou removida na v4 voltava a partir de um JSON legado obsoleto.

    Cada teste aqui adultera a legada à mão (nenhum fluxo do projeto escreve
    só nela hoje — ver auditoria da etapa 2) e confirma que o valor obsoleto
    NÃO volta para a v4 em nenhum dos fluxos migrados nas etapas 1/1b/1c.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _account(self, conn, user_id, **cols):
        campos = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO economy (user_id, user_name, {campos}) VALUES (?, ?, {marks})",
            (user_id, "Tester", *cols.values()),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    # ------------------------------------------------------------ inventário
    def test_consumed_item_does_not_come_back_from_legacy_json(self):
        conn = self._make_conn()
        user_id = 8001
        self._account(conn, user_id, inventory=json.dumps({"energetico": 2}))
        self.assertEqual(get_inventory(conn, user_id).get("energetico"), 2)

        # Consome tudo pela v4 (fluxo do ConsumeSelect / Galdino).
        set_inventory_item(conn, user_id, "energetico", 0)
        self.assertNotIn("energetico", get_inventory(conn, user_id))

        # Legada ficou para trás com o item ainda lá.
        conn.execute(
            "UPDATE economy SET inventory = ? WHERE user_id = ?",
            (json.dumps({"energetico": 2}), user_id),
        )
        conn.commit()

        self.assertNotIn(
            "energetico",
            get_inventory(conn, user_id),
            "item consumido ressuscitou a partir do JSON legado",
        )

    def test_partially_spent_stack_is_not_topped_back_up(self):
        conn = self._make_conn()
        user_id = 8002
        self._account(conn, user_id, inventory=json.dumps({"isca": 10}))
        add_inventory_item(conn, user_id, "isca", -7)
        self.assertEqual(get_inventory(conn, user_id)["isca"], 3)

        conn.execute(
            "UPDATE economy SET inventory = ? WHERE user_id = ?",
            (json.dumps({"isca": 10}), user_id),
        )
        conn.commit()

        self.assertEqual(get_inventory(conn, user_id)["isca"], 3)

    # -------------------------------------------------------------- carteira
    def test_spent_wallet_is_not_restored_from_legacy(self):
        conn = self._make_conn()
        user_id = 8003
        self._account(conn, user_id, wallet=1000)
        self.assertTrue(try_spend_wallet(conn, user_id, 800))
        self.assertEqual(get_wallet(conn, user_id), 200)

        conn.execute("UPDATE economy SET wallet = ? WHERE user_id = ?", (1000, user_id))
        conn.commit()

        self.assertEqual(get_wallet(conn, user_id), 200)

    # ---------------------------------------------------------------- sucata
    def test_spent_scrap_is_not_restored_from_legacy(self):
        """Bug real citado em minigames.py: o craft saía de graça porque o
        ensure_user do add_inventory_item seguinte trazia o scrap antigo."""
        conn = self._make_conn()
        user_id = 8004
        self._account(conn, user_id, scrap=50)
        modify_scrap(conn, user_id, -30)
        self.assertEqual(get_scrap(conn, user_id), 20)

        conn.execute("UPDATE economy SET scrap = ? WHERE user_id = ?", (50, user_id))
        conn.commit()

        add_inventory_item(conn, user_id, "resultado_craft", 1)

        self.assertEqual(
            get_scrap(conn, user_id), 20, "sucata gasta voltou — craft de graça"
        )

    # ------------------------------------------------------------- armadilha
    def test_collected_trap_does_not_return_to_ready(self):
        conn = self._make_conn()
        user_id = 8005
        self._account(conn, user_id, wallet=0)
        set_trap(conn, user_id, {"type": "covo_basico", "status": "ready", "timer_end": 0})
        set_trap(conn, user_id, {"type": "covo_basico", "status": "cooldown", "timer_end": 99})
        self.assertEqual(get_trap(conn, user_id)["status"], "cooldown")

        conn.execute(
            "UPDATE economy SET afk_trap = ? WHERE user_id = ?",
            (json.dumps({"type": "covo_basico", "status": "ready", "timer_end": 0}), user_id),
        )
        conn.commit()

        self.assertEqual(
            get_trap(conn, user_id)["status"],
            "cooldown",
            "armadilha voltou para 'ready' — coleta duplicada",
        )

    def test_removed_trap_stays_removed(self):
        conn = self._make_conn()
        user_id = 8006
        self._account(conn, user_id, wallet=0)
        set_trap(conn, user_id, {"type": "covo_basico", "status": "idle", "timer_end": 0})
        set_trap(conn, user_id, None)
        self.assertEqual(get_trap(conn, user_id), {})

        conn.execute(
            "UPDATE economy SET afk_trap = ? WHERE user_id = ?",
            (json.dumps({"type": "covo_basico", "status": "idle", "timer_end": 0}), user_id),
        )
        conn.commit()

        self.assertEqual(get_trap(conn, user_id), {})

    # --------------------------------------------------------- rank / XP
    def test_promoted_rank_is_not_reverted_by_legacy(self):
        conn = self._make_conn()
        user_id = 8007
        self._account(conn, user_id, guild_rank="E", guild_xp=600)
        set_guild_rank(conn, user_id, "D", 100)

        conn.execute(
            "UPDATE economy SET guild_rank = ?, guild_xp = ? WHERE user_id = ?",
            ("E", 600, user_id),
        )
        conn.commit()

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "D", "xp": 100})

    # ------------------------------------------------------------ vara
    def test_equipped_rod_is_not_reverted_by_legacy(self):
        conn = self._make_conn()
        user_id = 8008
        self._account(conn, user_id, current_rod="vara_bambu")
        set_current_rod(conn, user_id, "vara_ouro")
        self.assertEqual(get_current_rod(conn, user_id), "vara_ouro")

        conn.execute(
            "UPDATE economy SET current_rod = ? WHERE user_id = ?", ("vara_bambu", user_id)
        )
        conn.commit()

        self.assertEqual(get_current_rod(conn, user_id), "vara_ouro")

    # ------------------------------------------- XP de pesca fim-a-fim
    async def test_fishing_xp_and_fish_count_survive_a_stale_legacy_row(self):
        """Fluxo completo de /eco pescar com a legada adulterada logo antes:
        o comando não pode partir do estado velho."""
        conn = self._make_conn()
        user_id = 8009
        self._account(conn, user_id, guild_rank="E", guild_xp=100, fish_count=5)
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        set_guild_rank(conn, user_id, "E", 300)

        # Legada adulterada para valores diferentes dos da v4 nos dois campos.
        conn.execute(
            "UPDATE economy SET guild_xp = ?, fish_count = ? WHERE user_id = ?",
            (100, 99, user_id),
        )
        conn.commit()

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=user_id, name="Tester", display_name="Tester"),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        real_choice = economia.random.choice

        def low_tier(seq):
            cands = [i for i in seq if isinstance(i, tuple) and len(i) == 6 and 0 < i[4] <= 1]
            return cands[0] if cands else real_choice(seq)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.random, "choice", side_effect=low_tier):
            await economia.pescar.callback(interaction)

        row = conn.execute(
            "SELECT guild_xp, fish_count FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        # Parte de 300 (v4), não de 100 (legada adulterada); +2 de XP do tier baixo.
        self.assertEqual(row["guild_xp"], 302)
        # 5 vieram da importação de criação; a pescaria soma 1. O 99 plantado
        # na legada não pode entrar na conta.
        self.assertEqual(row["fish_count"], 6)

    # ------------------------------- criação de conta ainda importa a legada
    def test_pre_v4_account_is_still_imported_on_first_touch(self):
        """Contraprova: o caminho de criação continua trazendo a legada — é o
        que faz uma conta antiga (pré-v4) não nascer zerada."""
        conn = self._make_conn()
        user_id = 8010
        conn.execute(
            """
            INSERT INTO economy (user_id, user_name, wallet, scrap, guild_rank, guild_xp,
                                 inventory, current_rod, afk_trap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, "Antigo", 777, 42, "C", 1234,
                json.dumps({"energetico": 3}), "vara_ouro",
                json.dumps({"type": "covo_basico", "status": "ready", "timer_end": 0}),
            ),
        )
        conn.commit()
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        )

        ensure_user(conn, user_id, "Antigo")

        self.assertEqual(get_wallet(conn, user_id), 777)
        self.assertEqual(get_scrap(conn, user_id), 42)
        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "C", "xp": 1234})
        self.assertEqual(get_inventory(conn, user_id).get("energetico"), 3)
        self.assertEqual(get_current_rod(conn, user_id), "vara_ouro")
        self.assertEqual(get_trap(conn, user_id).get("status"), "ready")

    def test_sibling_rows_are_recreated_even_for_an_existing_user(self):
        """set_current_rod/set_cooldown/try_upgrade_rod fazem UPDATE puro e
        viram no-op se a linha irmã sumir. Antes o sync_user_from_economy de
        toda chamada recriava por efeito colateral; agora o INSERT OR IGNORE
        é explícito e incondicional."""
        conn = self._make_conn()
        user_id = 8011
        self._account(conn, user_id, wallet=0)

        conn.execute("DELETE FROM user_rods WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM rod_upgrades WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_cooldowns WHERE user_id = ?", (user_id,))
        conn.commit()

        set_current_rod(conn, user_id, "vara_ouro")

        self.assertEqual(get_current_rod(conn, user_id), "vara_ouro")
        # Todos os níveis nascem zerados. Checa os valores em vez do dicionário
        # inteiro: o teste é sobre a linha irmã ter sido recriada, e travar a
        # forma do dict fazia uma coluna nova quebrar um teste de ressurreição
        # de linha.
        upgrades = get_rod_upgrades(conn, user_id)
        self.assertTrue(all(v == 0 for v in upgrades.values()), upgrades)
        self.assertIsNone(get_cooldowns(conn, user_id)["last_fish"])



class LegacyReadRetirementTests(unittest.IsolatedAsyncioTestCase):
    """Etapa 3 — os fluxos que liam `economy` agora leem a v4.

    O que importa validar aqui: o jogador vê a MESMA coisa de antes, e a
    leitura passou a refletir a v4 mesmo quando a legada está defasada (o que
    antes produzia tela desatualizada).
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        user = SimpleNamespace(
            id=user_id, name=name, display_name=name, avatar=None,
            display_avatar=SimpleNamespace(url="http://x/a.png"),
        )
        return SimpleNamespace(
            user=user,
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _account(self, conn, user_id, name="Tester", **cols):
        campos = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        sufixo = f", {campos}" if cols else ""
        valores = f", {marks}" if cols else ""
        conn.execute(
            f"INSERT INTO economy (user_id, user_name{sufixo}) VALUES (?, ?{valores})",
            (user_id, name, *cols.values()),
        )
        conn.commit()
        ensure_user(conn, user_id, name)

    # ------------------------------------------------------------ /eco saldo
    async def test_saldo_shows_wallet_fish_rod_baits_and_backpack_from_v4(self):
        conn = self._make_conn()
        user_id = 9001
        self._account(
            conn, user_id, "Tester",
            wallet=1234, fish_count=7, current_rod="vara_ouro",
            inventory=json.dumps({"isca": 4, "energetico": 2}),
        )

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.saldo.callback(interaction)

        _, kwargs = interaction.response.send_message.call_args
        embed = kwargs["embed"]
        financas = next(f for f in embed.fields if "Finanças" in f.name)
        self.assertIn("1234", financas.value)
        self.assertIn("7", financas.value)

        equipado = next(f for f in embed.fields if "Equipado" in f.name)
        self.assertIn("Vara de Ouro", equipado.value)
        # `baits` saía de economy.baits, que era derivada de inv['isca'].
        self.assertIn("**4** Iscas", equipado.value)

        mochila = next(f for f in embed.fields if "Mochila" in f.name)
        self.assertIn("Energético", mochila.value)

    async def test_saldo_reflects_v4_when_legacy_row_is_stale(self):
        """O ganho concreto da etapa 3: antes a tela vinha da legada, então um
        estado defasado nela aparecia para o jogador."""
        conn = self._make_conn()
        user_id = 9002
        self._account(conn, user_id, "Tester", wallet=100, inventory=json.dumps({"isca": 1}))
        modify_wallet(conn, user_id, 900)
        add_inventory_item(conn, user_id, "isca", 9)

        conn.execute(
            "UPDATE economy SET wallet = ?, baits = ?, inventory = ? WHERE user_id = ?",
            (100, 1, json.dumps({"isca": 1}), user_id),
        )
        conn.commit()

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.saldo.callback(interaction)

        _, kwargs = interaction.response.send_message.call_args
        embed = kwargs["embed"]
        self.assertIn("1000", next(f for f in embed.fields if "Finanças" in f.name).value)
        self.assertIn("**10** Iscas", next(f for f in embed.fields if "Equipado" in f.name).value)

    async def test_saldo_keeps_legacy_display_name_keys_working(self):
        """Chaves antigas por nome de exibição ('Teclado do Arquiteto') existem
        igual em user_inventory — sync_user_from_economy copia a chave crua —
        então o mapa name_to_key continua sendo necessário."""
        conn = self._make_conn()
        user_id = 9003
        self._account(
            conn, user_id, "Tester",
            inventory=json.dumps({"Teclado do Arquiteto": 1}),
        )
        self.assertIn("Teclado do Arquiteto", get_inventory(conn, user_id))

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.saldo.callback(interaction)

        _, kwargs = interaction.response.send_message.call_args
        mochila = next(f for f in kwargs["embed"].fields if "Mochila" in f.name)
        self.assertIn("MÍTICO", mochila.value)

    async def test_saldo_refuses_without_account_and_does_not_create_one(self):
        conn = self._make_conn()
        user_id = 9004

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.saldo.callback(interaction)

        args, _ = interaction.response.send_message.call_args
        self.assertIn("sem conta bancária", args[0])
        # O portão não pode criar a conta que ele está barrando.
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        )

    # ------------------------------------------------------------- /eco rank
    async def test_rank_leaderboard_orders_by_v4_values(self):
        conn = self._make_conn()
        for uid, nome, wallet, peixes in [
            (9101, "Rico", 5000, 1),
            (9102, "Medio", 900, 50),
            (9103, "Pobre", 10, 5),
        ]:
            self._account(conn, uid, nome, wallet=wallet, fish_count=peixes)

        interaction = self._make_interaction(9101)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.rank.callback(interaction)

        _, kwargs = interaction.response.send_message.call_args
        campos = {f.name: f.value for f in kwargs["embed"].fields}
        dinheiro = next(v for k, v in campos.items() if "💰" in k or "Rico" in v)
        self.assertLess(dinheiro.index("Rico"), dinheiro.index("Medio"))
        self.assertLess(dinheiro.index("Medio"), dinheiro.index("Pobre"))

    def test_get_top_players_rejects_arbitrary_columns(self):
        conn = self._make_conn()
        with self.assertRaises(ValueError):
            get_top_players(conn, "wallet; DROP TABLE users", 10)

    # ------------------------------------------------------ nomes de membros
    def test_get_user_names_skips_unknown_ids_and_does_not_create_rows(self):
        conn = self._make_conn()
        self._account(conn, 9201, "Alfa")
        self._account(conn, 9202, "Beta")

        nomes = get_user_names(conn, [9201, 9202, 9299])

        self.assertEqual(nomes, {9201: "Alfa", 9202: "Beta"})
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (9299,)).fetchone()
        )

    def test_get_user_names_handles_empty_list(self):
        self.assertEqual(get_user_names(self._make_conn(), []), {})

    # --------------------------------------------- selo da guilda (v4)
    async def test_guild_seal_is_detected_from_v4_inventory(self):
        """O selo comprado/presenteado vai para user_inventory; antes só era
        visto se estivesse no JSON legado."""
        conn = self._make_conn()
        user_id = 9301
        self._account(conn, user_id, "Tester")
        add_inventory_item(conn, user_id, "selo_capitao", 1)
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'inicio')",
            (user_id,),
        )
        conn.commit()

        view = economia.GuildView(user_id, "Tester")
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await view.talk_jenna.callback(interaction)

        chapter = conn.execute(
            "SELECT current_chapter FROM quest_progress WHERE user_id = ?", (user_id,)
        ).fetchone()["current_chapter"]
        self.assertEqual(chapter, "acesso_liberado")

    async def test_card_btn_shows_rank_from_v4(self):
        conn = self._make_conn()
        user_id = 9302
        self._account(conn, user_id, "Tester", guild_rank="E", guild_xp=100)
        set_guild_rank(conn, user_id, "D", 250)
        conn.execute(
            "UPDATE economy SET guild_rank = ?, guild_xp = ? WHERE user_id = ?",
            ("E", 100, user_id),
        )
        conn.commit()

        view = economia.GuildView(user_id, "Tester")
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await view.card_btn.callback(interaction)

        _, kwargs = interaction.response.send_message.call_args
        rank_field = next(f for f in kwargs["embed"].fields if "Rank Atual" in f.name)
        # Legada plantada com E/100; o cartão tem que mostrar o D/250 da v4.
        self.assertIn("**D**", rank_field.value)
        progresso = next(f for f in kwargs["embed"].fields if "Progresso" in f.name)
        self.assertIn("250", progresso.value)


class LerGarrafaV4Tests(unittest.IsolatedAsyncioTestCase):
    """/ler_garrafa passou a olhar user_inventory em vez do JSON legado."""

    def _make_conn(self):
        return _make_pescar_conn()

    def _cog(self, conn):
        from cogs.sistema import SistemaCog

        return SistemaCog(SimpleNamespace(db_conn=conn))

    def _make_interaction(self, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name="Tester", display_name="Tester"),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_bottle_in_v4_inventory_is_accepted(self):
        conn = self._make_conn()
        user_id = 9401
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "T"))
        conn.commit()
        ensure_user(conn, user_id, "T")
        add_inventory_item(conn, user_id, "garrafa_incrustada", 1)

        interaction = self._make_interaction(user_id)
        await self._cog(conn).ler_garrafa.callback(self._cog(conn), interaction)

        interaction.response.defer.assert_awaited()

    async def test_without_bottle_is_refused(self):
        conn = self._make_conn()
        user_id = 9402
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "T"))
        conn.commit()
        ensure_user(conn, user_id, "T")

        interaction = self._make_interaction(user_id)
        await self._cog(conn).ler_garrafa.callback(self._cog(conn), interaction)

        args, _ = interaction.response.send_message.call_args
        self.assertIn("não tem nenhuma", args[0])

    async def test_quest_progress_route_still_works(self):
        """A rota original (quest_progress.inventory) não foi tocada."""
        conn = self._make_conn()
        user_id = 9403
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "T"))
        conn.execute(
            "INSERT INTO quest_progress (user_id, inventory) VALUES (?, ?)",
            (user_id, json.dumps({"garrafa_incrustada": 1})),
        )
        conn.commit()
        ensure_user(conn, user_id, "T")

        interaction = self._make_interaction(user_id)
        await self._cog(conn).ler_garrafa.callback(self._cog(conn), interaction)

        interaction.response.defer.assert_awaited()


class PortaoXpGarrafaTests(unittest.IsolatedAsyncioTestCase):
    """Regressão do portão de XP de guilda travado em conta nova.

    `ensure_user()` cria as linhas v4 do jogador mas não a de
    `quest_progress`. O trecho da garrafa em `/eco pescar` gravava o capítulo
    com um `UPDATE ... WHERE user_id = ?`, que numa conta nova não encontrava
    linha nenhuma e voltava afetando 0 linhas em silêncio — o capítulo ficava
    NULL e o `if row['current_chapter'] in [...]` que libera XP de guilda
    nunca abria, por mais que o jogador pescasse.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Novato"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _cog_sistema(self, conn):
        from cogs.sistema import SistemaCog

        return SistemaCog(SimpleNamespace(db_conn=conn))

    async def _pescar(self, conn, user_id, com_garrafa):
        """Uma pescaria determinística de tier 1 (2 XP de guilda quando o
        portão está aberto). `com_garrafa` força/bloqueia o gatilho da quest
        pelo único ramo que ele consulta o RNG (`randint(1, 4) == 1`)."""
        real_choice = economia.random.choice
        real_randint = economia.random.randint

        def tier1(seq):
            cands = [i for i in seq if isinstance(i, tuple) and len(i) == 6 and i[4] == 1]
            return cands[0] if cands else real_choice(seq)

        def randint(a, b):
            if (a, b) == (1, 4):
                return 1 if com_garrafa else 2
            return real_randint(a, b)

        # Libera o cooldown de pesca para as chamadas encadeadas do mesmo teste.
        conn.execute("UPDATE user_cooldowns SET last_fish = NULL WHERE user_id = ?", (user_id,))
        conn.commit()

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)), \
                patch.object(economia.random, "choice", side_effect=tier1), \
                patch.object(economia.random, "randint", side_effect=randint):
            await economia.pescar.callback(interaction)
        return interaction

    def _chapter(self, conn, user_id):
        row = conn.execute(
            "SELECT current_chapter FROM quest_progress WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["current_chapter"] if row else None

    async def test_bottle_creates_quest_progress_row_on_new_account(self):
        """O caso que quebrava: conta nova, sem linha em quest_progress."""
        conn = self._make_conn()
        user_id = 9501

        self.assertIsNone(self._chapter(conn, user_id))
        await self._pescar(conn, user_id, com_garrafa=True)

        self.assertEqual(get_inventory(conn, user_id).get("garrafa_incrustada"), 1)
        self.assertEqual(self._chapter(conn, user_id), "garrafa_encontrada")

    async def test_xp_flows_on_the_cast_after_the_bottle(self):
        """O portão abre de verdade: a pescaria seguinte já credita XP.

        A pescaria que traz a garrafa ainda não pontua — o capítulo é lido no
        início do comando, antes do gatilho — e isso é intencional; o que não
        podia acontecer é a seguinte também sair zerada.
        """
        conn = self._make_conn()
        user_id = 9502

        await self._pescar(conn, user_id, com_garrafa=True)
        xp_na_garrafa = get_guild_rank(conn, user_id)["xp"]

        await self._pescar(conn, user_id, com_garrafa=False)
        xp_depois = get_guild_rank(conn, user_id)["xp"]

        self.assertEqual(xp_na_garrafa, 0)
        self.assertEqual(xp_depois, 2, "XP de guilda continuou travado depois da garrafa")

    async def test_bottle_overwrites_inicio_chapter(self):
        """Linha já existente em 'inicio' (o default do schema) tem que subir."""
        conn = self._make_conn()
        user_id = 9503
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'inicio')",
            (user_id,),
        )
        conn.commit()

        await self._pescar(conn, user_id, com_garrafa=True)

        self.assertEqual(self._chapter(conn, user_id), "garrafa_encontrada")

    async def test_bottle_does_not_regress_an_advanced_chapter(self):
        """Quem já entregou o selo não pode voltar para 'garrafa_encontrada'."""
        conn = self._make_conn()
        user_id = 9504
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()

        await self._pescar(conn, user_id, com_garrafa=True)

        self.assertEqual(self._chapter(conn, user_id), "acesso_liberado")

    async def test_ler_garrafa_persists_chapter_and_keeps_xp_flowing(self):
        """Validação ponta a ponta: pescar a garrafa, ler, e continuar pescando."""
        conn = self._make_conn()
        user_id = 9505

        await self._pescar(conn, user_id, com_garrafa=True)

        interaction = self._make_interaction(user_id)
        cog = self._cog_sistema(conn)
        await cog.ler_garrafa.callback(cog, interaction)

        row = conn.execute(
            "SELECT current_chapter, inventory FROM quest_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        self.assertEqual(row["current_chapter"], "acesso_liberado")
        self.assertEqual(json.loads(row["inventory"]).get("selo_capitao"), 1)

        antes = get_guild_rank(conn, user_id)["xp"]
        await self._pescar(conn, user_id, com_garrafa=False)
        self.assertEqual(get_guild_rank(conn, user_id)["xp"], antes + 2)


class VaraInicialTests(unittest.IsolatedAsyncioTestCase):
    """Item 1 do balanceamento: a faixa de entrada estava fora da escada.

    Uma vara de tier 0 zera o filtro `0 < tier <= 0` do ramo "não deu lixo",
    e o fallback devolvia as 20 entradas de tier 0 — metade delas lixo. Efeito
    colateral: a Vara de Treino, única vara COMPRÁVEL de tier 0, dividia o pool
    com a vara grátis e, por ser 20% mais lenta, rendia MENOS que ela — a
    primeira compra do jogo era um downgrade.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Novato"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    # ------------------------------------------------ fallback do tier 0
    def test_tier0_fallback_has_no_trash(self):
        fallback = [p for p in economia.FISH_DB if p[4] == 0 and p[0] not in economia.TRASH_ITEMS]
        self.assertEqual(len(fallback), 10)
        self.assertTrue(all(p[0] not in economia.TRASH_ITEMS for p in fallback))

    async def test_fishing_with_a_tier0_rod_never_returns_trash_from_the_fish_branch(self):
        """Força o ramo "não deu lixo" (trash_chance 0 via Firewall) e confere
        que 200 lances com a vara inicial não trazem uma peça de lixo."""
        conn = self._make_conn()
        user_id = 9801
        ensure_user(conn, user_id, "Novato")
        add_inventory_item(conn, user_id, "firewall", 300)

        capturas = []
        real_choice = economia.random.choice

        def espiao(seq):
            escolha = real_choice(seq)
            if isinstance(escolha, tuple) and len(escolha) == 6:
                capturas.append(escolha[0])
            return escolha

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)), \
                patch.object(economia.random, "choice", side_effect=espiao):
            for _ in range(200):
                conn.execute("UPDATE user_cooldowns SET last_fish = NULL WHERE user_id = ?", (user_id,))
                conn.commit()
                await economia.pescar.callback(self._make_interaction(user_id))

        self.assertEqual(len(capturas), 200)
        lixo = [c for c in capturas if c in economia.TRASH_ITEMS]
        self.assertEqual(lixo, [], f"fallback do tier 0 ainda devolve lixo: {set(lixo)}")

    # ------------------------------------------------ stats das varas
    def test_bambu_trash_lowered(self):
        self.assertEqual(economia.ROD_STATS["vara_bambu"]["trash"], 35)
        self.assertEqual(economia.ROD_STATS["vara_bambu"]["tier"], 0)
        self.assertEqual(economia.ROD_STATS["vara_bambu"]["price"], 0)

    def test_treino_is_now_tier_1(self):
        t = economia.ROD_STATS["vara_treino"]
        self.assertEqual(t["tier"], 1)
        self.assertEqual(t["cd"], 1.3)
        self.assertEqual(t["trash"], 55)
        self.assertEqual(t["price"], 250)

    def test_shop_entry_agrees_with_rod_stats(self):
        """A loja mostra o tier para o jogador; divergir de ROD_STATS mente
        sobre o que a compra faz."""
        for key, item in economia.SHOP_ITEMS.items():
            if item.get("type") != "rod":
                continue
            with self.subTest(vara=key):
                self.assertEqual(item["tier"], economia.ROD_STATS[key]["tier"])
                self.assertEqual(item["price"], economia.ROD_STATS[key]["price"])

    def test_no_purchasable_rod_is_tier_zero(self):
        """Vara comprável de tier 0 divide o pool com a vara grátis e vira
        armadilha — foi exatamente o caso da Treino."""
        for key, r in economia.ROD_STATS.items():
            if r["price"] > 0:
                with self.subTest(vara=key):
                    self.assertGreater(r["tier"], 0)

    def test_entry_ladder_is_monotonic(self):
        """Cada compra da faixa de entrada tem que render mais por hora que a
        anterior. Antes a escada começava DESCENDO (185,2 -> 159,8)."""
        escada = ["vara_bambu", "vara_treino", "vara_plastico", "vara_fibra", "vara_pesada"]
        taxas = []
        for k in escada:
            r = economia.ROD_STATS[k]
            taxas.append(_ev_por_hora(k))
        for anterior, atual, ka, kb in zip(taxas, taxas[1:], escada, escada[1:]):
            with self.subTest(de=ka, para=kb):
                self.assertGreater(
                    atual, anterior,
                    f"{economia.ROD_STATS[kb]['name']} rende menos que {economia.ROD_STATS[ka]['name']}",
                )

    def test_isca_price_lowered(self):
        self.assertEqual(economia.SHOP_ITEMS["isca"]["price"], 25)


def _ev_por_hora(rod_key):
    """EV analítico de Sachês/hora de uma vara, sem upgrades e sem isca.

    Espelha a matemática de `pescar` (clima ponderado 70/20/10, pool uniforme,
    média do intervalo randint) — serve para as asserções de ORDEM da escada,
    não como número exibido ao jogador.
    """
    r = economia.ROD_STATS[rod_key]
    climas = {"normal": (1.0, 1.0, 0, 0.7), "bad": (0.5, 2.0, 0, 0.2), "good": (1.5, 0.5, 1, 0.1)}
    iniciais = [p for p in economia.FISH_DB if p[4] == 0 and p[0] not in economia.TRASH_ITEMS]
    media = lambda p: (p[1] + p[2]) / 2
    ev = 0.0
    for _, (lm, tm, tb, peso) in climas.items():
        pt = max(0.0, min(100.0, r["trash"] * tm)) / 100.0
        mult = r["luck"] * lm
        parcial = pt * (1 - economia.TRASH_ROLL_RATIO) * (sum(map(media, iniciais)) / len(iniciais)) * mult
        pool = [p for p in economia.FISH_DB if 0 < p[4] <= r["tier"] + tb] or iniciais
        parcial += (1 - pt) * (sum(media(p) for p in pool if p[0] not in economia.TRASH_ITEMS) / len(pool)) * mult
        ev += peso * parcial
    return ev * 3600 / int(300 * r["cd"])


class CovoWaitTimeTests(unittest.IsolatedAsyncioTestCase):
    """Regressão do Covo que entregava na hora.

    `wait_time` estava `00` — zero, apesar do comentário ao lado dizer "5
    minutos". A armadilha lançada nascia em 'working' com `timer_end = agora`,
    a re-renderização seguinte já via `remaining <= 0` e promovia para 'ready'
    na mesma interação: as 5 capturas saíam instantaneamente e o único freio
    era o `reset_time`. Valor correto: 600s.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _seed(self, conn, user_id):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (user_id, "Tester", 500),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    def test_covo_wait_time_is_ten_minutes(self):
        self.assertEqual(economia.TRAP_TYPES["covo_basico"]["wait_time"], 600)

    def test_no_trap_has_a_zero_wait_time(self):
        """Qualquer armadilha com espera 0 volta a entregar na hora."""
        for key, stats in economia.TRAP_TYPES.items():
            with self.subTest(armadilha=key):
                self.assertGreater(stats["wait_time"], 0)

    async def test_launching_the_covo_leaves_it_working_not_ready(self):
        """O caminho que o bug tornava instantâneo: lançar e voltar no painel."""
        conn = self._make_conn()
        user_id = 9701
        self._seed(conn, user_id)
        set_trap(conn, user_id, {"type": "covo_basico", "status": "idle", "timer_end": 0})

        view = economia.GaldinoView(user_id, "Tester")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            # Abre o painel (estado idle) e clica em "Jogar".
            abrir = self._make_interaction(user_id)
            await view.trap_manager.callback(abrir)
            botao = abrir.response.send_message.call_args.kwargs["view"].children[0]
            await botao.callback(self._make_interaction(user_id))

            trap = get_trap(conn, user_id)
            self.assertEqual(trap["status"], "working")
            restante = trap["timer_end"] - datetime.now().timestamp()
            self.assertGreater(restante, 540, "armadilha lançada já estava quase pronta")

            # Voltar ao painel não pode promover para 'ready'.
            voltar = self._make_interaction(user_id)
            await view.trap_manager.callback(voltar)
            self.assertEqual(get_trap(conn, user_id)["status"], "working")

        embed = voltar.response.send_message.call_args.kwargs["embed"]
        self.assertIn("Trabalhando", embed.description)


class LimiarDeRankTests(unittest.TestCase):
    """A escada de ranks é definida por `req_xp` do rank de DESTINO.

    Os dois pontos que resolviam promoção comparavam o XP com o `req_xp` do
    rank ATUAL, deslocando a escada um degrau para baixo: F (req_xp 0)
    promovia no primeiro lance e o rank A saía por 16.000 XP acumulados em
    vez dos 41.000 da tabela.
    """

    def test_cost_to_leave_each_rank_is_the_next_ranks_requirement(self):
        esperado = {
            "F": ("E", 500),
            "E": ("D", 1500),
            "D": ("C", 4000),
            "C": ("B", 10000),
            "B": ("A", 25000),
        }
        for rank, alvo in esperado.items():
            with self.subTest(rank=rank):
                self.assertEqual(economia.next_rank_requirement(rank), alvo)

    def test_rank_a_is_the_top_of_the_ladder(self):
        self.assertEqual(economia.next_rank_requirement("A"), (None, None))

    def test_rank_a_costs_the_tables_real_requirement(self):
        """O req_xp 25.000 do rank A tinha virado número morto: ninguém lia."""
        _, custo = economia.next_rank_requirement("B")
        self.assertEqual(custo, economia.GUILD_RANKS["A"]["req_xp"])
        self.assertEqual(custo, 25000)

    def test_full_ladder_costs_41000_accumulated(self):
        """F -> A somando cada degrau: 500+1500+4000+10000+25000."""
        total, rank = 0, "F"
        while True:
            proximo, custo = economia.next_rank_requirement(rank)
            if not proximo:
                break
            total += custo
            rank = proximo
        self.assertEqual(rank, "A")
        self.assertEqual(total, 41000)

    def test_unknown_rank_falls_back_to_f(self):
        """Rank fora da escada (lixo no banco) não pode explodir."""
        self.assertEqual(economia.next_rank_requirement("ZZZ"), ("E", 500))


class PromocaoNaFronteiraTests(unittest.IsolatedAsyncioTestCase):
    """Promoção na fronteira exata de XP, pelos dois caminhos que promovem."""

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _seed(self, conn, user_id, rank, xp):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, ?, ?)",
            (user_id, "Tester", rank, xp),
        )
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    async def _ask_promo(self, conn, user_id):
        view = economia.GuildView(user_id, "Tester")
        abrir = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await view.talk_jenna.callback(abrir)
            select = abrir.response.send_message.call_args.kwargs["view"].children[0]
            select._values = ["ask_promo"]
            await select.callback(self._make_interaction(user_id))

    async def _pescar(self, conn, user_id):
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await economia.pescar.callback(self._make_interaction(user_id))

    async def test_jenna_promotes_exactly_at_the_threshold(self):
        conn = self._make_conn()
        self._seed(conn, 7601, "E", 1500)
        await self._ask_promo(conn, 7601)
        self.assertEqual(get_guild_rank(conn, 7601), {"rank": "D", "xp": 0})

    async def test_jenna_refuses_one_xp_below_the_threshold(self):
        conn = self._make_conn()
        self._seed(conn, 7602, "E", 1499)
        await self._ask_promo(conn, 7602)
        self.assertEqual(get_guild_rank(conn, 7602), {"rank": "E", "xp": 1499})

    async def test_jenna_promotes_b_to_a_only_at_25000(self):
        """O degrau que o bug mais encurtava: B->A saía por 10.000."""
        conn = self._make_conn()
        self._seed(conn, 7603, "B", 24999)
        await self._ask_promo(conn, 7603)
        self.assertEqual(get_guild_rank(conn, 7603)["rank"], "B")

        conn2 = self._make_conn()
        self._seed(conn2, 7604, "B", 25000)
        await self._ask_promo(conn2, 7604)
        self.assertEqual(get_guild_rank(conn2, 7604), {"rank": "A", "xp": 0})

    async def test_fishing_does_not_promote_f_on_the_first_cast(self):
        """Com o req_xp do rank atual (F = 0) a condição era sempre verdadeira
        e todo jogador virava rank E no primeiro peixe."""
        conn = self._make_conn()
        user_id = 7605
        self._seed(conn, user_id, "F", 0)

        # Vara inicial é tier 0, então a captura vale 2 XP de guilda.
        await self._pescar(conn, user_id)

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "F", "xp": 2})

    async def test_fishing_promotes_when_the_cast_crosses_the_threshold(self):
        conn = self._make_conn()
        user_id = 7606
        # 498 + 2 XP (peixe tier 0 da vara inicial) = 500, exatamente o req_xp
        # do rank E — a fronteira, sem sobra.
        self._seed(conn, user_id, "F", 498)

        await self._pescar(conn, user_id)

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "E", "xp": 0})


class ConsumeSelectInventoryTests(unittest.IsolatedAsyncioTestCase):
    """ConsumeSelect: o item consumido some da mochila e CONTINUA fora depois
    de um comando v4 seguinte.

    A suíte só checava cooldown e mensagem de sucesso — nenhuma asserção de
    que o item saiu do inventário. Foi por essa fresta que o bug do "item
    usado que volta" atravessou a suíte inteira: a remoção ia só para o JSON
    legado e o sync do comando seguinte trazia o item de volta.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def _account(self, conn, user_id, **itens):
        ensure_user(conn, user_id, "Tester")
        for chave, qtd in itens.items():
            add_inventory_item(conn, user_id, chave, qtd)

    async def _use(self, conn, user_id, item_key):
        select = economia.ConsumeSelect(user_id, get_inventory(conn, user_id))
        select._values = [item_key]
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)
        return interaction

    def _next_v4_command(self, conn, user_id):
        """Qualquer helper v4 que dispare o ciclo ensure_user + sync — é nesse
        ponto que a ressurreição acontecia."""
        modify_wallet(conn, user_id, 1)

    # ------------------------------------------------------------ energético
    async def test_using_one_of_a_stack_decrements_and_stays_decremented(self):
        conn = self._make_conn()
        user_id = 9501
        self._account(conn, user_id, energetico=3)

        await self._use(conn, user_id, "energetico")

        self.assertEqual(get_inventory(conn, user_id)["energetico"], 2)
        self._next_v4_command(conn, user_id)
        self.assertEqual(
            get_inventory(conn, user_id)["energetico"],
            2,
            "estoque voltou ao valor antigo depois do comando seguinte",
        )

    async def test_using_the_last_copy_removes_the_key_for_good(self):
        conn = self._make_conn()
        user_id = 9502
        self._account(conn, user_id, energetico=1)

        await self._use(conn, user_id, "energetico")

        self.assertNotIn("energetico", get_inventory(conn, user_id))
        self._next_v4_command(conn, user_id)
        self.assertNotIn("energetico", get_inventory(conn, user_id))
        # A cópia legada também some (é reescrita a partir da v4).
        legacy = conn.execute(
            "SELECT inventory FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        self.assertNotIn("energetico", json.loads(legacy["inventory"]))

    # ------------------------------------------------------- caixa misteriosa
    async def test_mystery_box_is_consumed_and_prize_persists(self):
        conn = self._make_conn()
        user_id = 9503
        self._account(conn, user_id, caixa_misteriosa=2)
        saldo_antes = get_wallet(conn, user_id)

        # Fixa o RESULTADO, não o randint. Desde que a caixa passou a sortear
        # a faixa com random() antes do valor, patchar só o randint deixava
        # ~8% das execuções caírem no ramo de item — que não paga Sachê — e o
        # teste falhava de forma intermitente. Este teste é sobre o item ser
        # consumido e o prêmio sobreviver ao sync seguinte, não sobre a
        # distribuição (essa é coberta em CaixaMisteriosaTests).
        with patch.object(
            economia, "abrir_caixa_misteriosa",
            return_value={"tipo": "dinheiro", "valor": 500, "item": None},
        ):
            await self._use(conn, user_id, "caixa_misteriosa")

        self.assertEqual(get_inventory(conn, user_id)["caixa_misteriosa"], 1)
        self.assertEqual(get_wallet(conn, user_id), saldo_antes + 500)

        self._next_v4_command(conn, user_id)
        self.assertEqual(get_inventory(conn, user_id)["caixa_misteriosa"], 1)
        self.assertEqual(get_wallet(conn, user_id), saldo_antes + 500 + 1)

    # ------------------------------------------------------------------ rede
    async def test_hand_net_is_consumed_and_profit_persists(self):
        conn = self._make_conn()
        user_id = 9504
        self._account(conn, user_id, rede=1)

        with patch.object(economia.random, "randint", return_value=30):
            await self._use(conn, user_id, "rede")

        self.assertNotIn("rede", get_inventory(conn, user_id))
        self.assertEqual(get_wallet(conn, user_id), 90)  # 3 x 30

        self._next_v4_command(conn, user_id)
        self.assertNotIn("rede", get_inventory(conn, user_id))
        self.assertEqual(get_wallet(conn, user_id), 91)

    # ------------------------------------- itens passivos NÃO são consumidos
    async def test_passive_buff_is_not_consumed_by_the_menu(self):
        """firewall/ima/chip são gastos por /eco pescar, não aqui — o menu só
        explica. Consumir aqui seria roubar o item do jogador."""
        conn = self._make_conn()
        user_id = 9505
        self._account(conn, user_id, firewall=1)

        interaction = await self._use(conn, user_id, "firewall")

        self.assertIn("passivo", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_inventory(conn, user_id)["firewall"], 1)
        self._next_v4_command(conn, user_id)
        self.assertEqual(get_inventory(conn, user_id)["firewall"], 1)

    async def test_bait_is_not_consumed_by_the_menu(self):
        conn = self._make_conn()
        user_id = 9506
        self._account(conn, user_id, isca=5)

        await self._use(conn, user_id, "isca")

        self.assertEqual(get_inventory(conn, user_id)["isca"], 5)
        self._next_v4_command(conn, user_id)
        self.assertEqual(get_inventory(conn, user_id)["isca"], 5)

    # ------------------------------------------------------------- recusas
    async def test_using_an_item_you_no_longer_have_is_refused(self):
        """A view fica aberta até 180s: o item pode ter sido gasto em outro
        fluxo entre abrir o menu e clicar."""
        conn = self._make_conn()
        user_id = 9507
        self._account(conn, user_id, energetico=1)

        select = economia.ConsumeSelect(user_id, {"energetico": 1})
        select._values = ["energetico"]
        # Gasto por fora depois do menu montado.
        add_inventory_item(conn, user_id, "energetico", -1)

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)

        self.assertIn("não tem mais", interaction.response.send_message.call_args.args[0])
        self.assertNotIn("energetico", get_inventory(conn, user_id))
        self.assertIsNotNone(get_cooldowns(conn, user_id) is not None)


class ValeriusShopPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """ValeriusShopSelect (loja de varas do NPC) — não tinha teste nenhum.
    Migrado na etapa 1: antes debitava `economy.wallet` e gravava em
    `economy.inventory`, e o sync do comando seguinte desfazia a compra.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def _buy(self, conn, user_id, rod_key):
        select = economia.ValeriusShopSelect(user_id)
        select._values = [rod_key]
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)
        return interaction

    async def test_purchase_debits_wallet_adds_rod_and_both_persist(self):
        conn = self._make_conn()
        user_id = 9601
        ensure_user(conn, user_id, "Tester")
        preco = economia.SHOP_ITEMS["vara_ouro"]["price"]
        modify_wallet(conn, user_id, preco + 100)

        interaction = await self._buy(conn, user_id, "vara_ouro")

        self.assertIn("Negócio Fechado", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_wallet(conn, user_id), 100)
        self.assertEqual(get_inventory(conn, user_id)["vara_ouro"], 1)

        # Comando v4 seguinte: a compra não pode desaparecer nem o saldo voltar.
        modify_wallet(conn, user_id, 0)
        self.assertEqual(get_wallet(conn, user_id), 100)
        self.assertEqual(get_inventory(conn, user_id)["vara_ouro"], 1)

    async def test_insufficient_funds_refuses_without_touching_state(self):
        conn = self._make_conn()
        user_id = 9602
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 10)

        interaction = await self._buy(conn, user_id, "vara_ouro")

        self.assertIn("Sem ouro", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_wallet(conn, user_id), 10)
        self.assertNotIn("vara_ouro", get_inventory(conn, user_id))

    async def test_purchase_does_not_auto_equip(self):
        """A vara comprada vai para a mochila; equipar é passo separado
        (RodSelect) — mesma regra já validada em /eco loja."""
        conn = self._make_conn()
        user_id = 9603
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, economia.SHOP_ITEMS["vara_ouro"]["price"])

        await self._buy(conn, user_id, "vara_ouro")

        self.assertEqual(get_current_rod(conn, user_id), "vara_bambu")


class TrapCollectPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Coleta da armadilha AFK: o loot entra na mochila, a rede sai de
    'ready', e nada disso volta atrás no comando seguinte.

    Sem isto, só as transições automáticas de timer tinham teste — o caminho
    que de fato pagava o loot (e onde estava o dupe) ficava descoberto.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def _open_and_get_collect_button(self, conn, user_id):
        view = economia.GaldinoView(user_id, "Tester")
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.trap_manager.callback(interaction)
        painel = interaction.response.send_message.call_args.kwargs["view"]
        return next(c for c in painel.children if c.label == "Puxar Rede")

    def _ready_trap(self, conn, user_id):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet) VALUES (?, ?, ?)",
            (user_id, "Tester", 0),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")
        set_trap(conn, user_id, {"type": "covo_basico", "status": "ready", "timer_end": 0})

    async def test_collect_pays_loot_closes_net_and_both_persist(self):
        conn = self._make_conn()
        user_id = 9701
        self._ready_trap(conn, user_id)

        botao = await self._open_and_get_collect_button(conn, user_id)
        click = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.random, "randint", return_value=100):  # não quebra
            await botao.callback(click)

        inv = get_inventory(conn, user_id)
        capacidade = economia.TRAP_TYPES["covo_basico"]["capacity"]
        self.assertEqual(sum(inv.values()), capacidade)
        self.assertEqual(get_trap(conn, user_id)["status"], "cooldown")

        # Comando v4 seguinte não pode devolver a rede para 'ready' (isso era
        # o dupe: coletar de novo o mesmo loot) nem sumir com o loot pago.
        modify_wallet(conn, user_id, 1)
        self.assertEqual(get_trap(conn, user_id)["status"], "cooldown")
        self.assertEqual(sum(get_inventory(conn, user_id).values()), capacidade)

    async def test_broken_net_state_also_persists(self):
        conn = self._make_conn()
        user_id = 9702
        self._ready_trap(conn, user_id)

        botao = await self._open_and_get_collect_button(conn, user_id)
        click = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.random, "randint", return_value=1):  # quebra
            await botao.callback(click)

        self.assertIn("CRACK", click.response.send_message.call_args.args[0])
        self.assertEqual(get_trap(conn, user_id)["status"], "broken")
        modify_wallet(conn, user_id, 1)
        self.assertEqual(get_trap(conn, user_id)["status"], "broken")

    async def test_second_click_on_a_stale_panel_is_refused(self):
        """O painel fica aberto: clicar duas vezes não pode pagar o loot duas
        vezes. O callback relê o estado na v4 antes de liberar."""
        conn = self._make_conn()
        user_id = 9703
        self._ready_trap(conn, user_id)

        botao = await self._open_and_get_collect_button(conn, user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.random, "randint", return_value=100):
            await botao.callback(self._make_interaction(user_id))
            total_apos_1a = sum(get_inventory(conn, user_id).values())

            segundo = self._make_interaction(user_id)
            await botao.callback(segundo)

        self.assertIn("Estado inválido", segundo.response.send_message.call_args.args[0])
        self.assertEqual(sum(get_inventory(conn, user_id).values()), total_apos_1a)



class GuildaEntryV4Tests(unittest.IsolatedAsyncioTestCase):
    """/eco guilda: a entrada garantia o rank inicial com um INSERT OR IGNORE
    direto em `economy`. Desde a etapa 3 nada no runtime lê guild_rank da
    legada, então aquele INSERT só criava linha legada órfã — trocado por
    ensure_user(), que dá a mesma garantia na v4 (users.guild_rank nasce 'F').
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def _enter(self, conn, user_id):
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia.os.path, "exists", return_value=False):
            await economia.guilda.callback(interaction)
        return interaction

    async def test_entry_via_quest_guarantees_the_v4_row_with_initial_rank(self):
        conn = self._make_conn()
        user_id = 9801
        # Rota da quest: libera sem exigir conta prévia.
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        )

        interaction = await self._enter(conn, user_id)

        _, kwargs = interaction.response.send_message.call_args
        self.assertIn("Guilda de Porto Solare", kwargs["embed"].title)
        # A garantia antiga ("rank inicial no banco") preservada, agora na v4.
        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "F", "xp": 0})

    async def test_entry_does_not_reset_an_existing_rank(self):
        conn = self._make_conn()
        user_id = 9802
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")
        set_guild_rank(conn, user_id, "C", 900)

        await self._enter(conn, user_id)

        self.assertEqual(get_guild_rank(conn, user_id), {"rank": "C", "xp": 900})

    async def test_entry_is_still_refused_without_quest_or_account(self):
        conn = self._make_conn()
        user_id = 9803

        interaction = await self._enter(conn, user_id)

        args, _ = interaction.response.send_message.call_args
        self.assertIn("Acesso Negado", args[0])
        # O portão não pode criar a conta que ele está barrando.
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        )



class VenderTests(unittest.IsolatedAsyncioTestCase):
    """Item 2: /eco vender, o escoadouro do peixe da armadilha.

    Peixe pescado vira Sachê no ato e nunca entra na mochila — quem enche a
    mochila de peixe é a armadilha AFK, e até aqui esse peixe não tinha
    destino nenhum.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _conta(self, conn, user_id):
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "Tester"))
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    async def _vender(self, conn, user_id, o_que="tudo"):
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.vender.callback(interaction, o_que)
        return interaction

    # ------------------------------------------------ tabela de preços
    def test_price_matches_median_times_tier_rate(self):
        for nome, (tier, v_min, v_max) in economia.FISH_BY_NAME.items():
            with self.subTest(peixe=nome):
                esperado = int((v_min + v_max) / 2 * economia.SELL_RATES[tier])
                self.assertEqual(economia.fish_sell_price(nome), esperado)

    def test_published_price_examples(self):
        """Os números que foram para a proposta aprovada."""
        for nome, preco in [
            ("Sardinha", 5), ("Lambari", 4), ("Tilápia", 7),
            ("Truta", 15), ("Tambaqui", 21), ("Lula", 24),
            ("Peixe-Palhaço", 33), ("Arraia", 50),
            ("Tubarão Branco", 225), ("Kraken", 1500),
        ]:
            with self.subTest(peixe=nome):
                self.assertEqual(economia.fish_sell_price(nome), preco)

    def test_rates_decrease_with_tier(self):
        """Peixe raro tem que valer a pena pescar, não farmar em armadilha."""
        taxas = [economia.SELL_RATES[t] for t in sorted(economia.SELL_RATES)]
        for anterior, atual in zip(taxas, taxas[1:]):
            self.assertLess(atual, anterior)

    def test_trash_is_not_sellable(self):
        for t in economia.TRASH_ITEMS:
            with self.subTest(item=t):
                self.assertEqual(economia.fish_sell_price(t), 0)
                self.assertNotIn(t, economia.FISH_BY_NAME)

    def test_unknown_item_is_worth_nothing(self):
        self.assertEqual(economia.fish_sell_price("vara_ouro"), 0)
        self.assertEqual(economia.fish_sell_price("nao_existe"), 0)

    # ------------------------------------------------ venda em lote
    async def test_sell_everything_pays_and_empties(self):
        conn = self._make_conn()
        user_id = 9901
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 10)   # 5 cada  = 50
        add_inventory_item(conn, user_id, "Truta", 4)       # 15 cada = 60
        add_inventory_item(conn, user_id, "Arraia", 2)      # 50 cada = 100

        await self._vender(conn, user_id, "tudo")

        self.assertEqual(get_wallet(conn, user_id), 210)
        inv = get_inventory(conn, user_id)
        for nome in ("Sardinha", "Truta", "Arraia"):
            self.assertEqual(inv.get(nome, 0), 0, f"{nome} continuou na mochila")

    async def test_sell_by_tier_leaves_other_tiers_alone(self):
        conn = self._make_conn()
        user_id = 9902
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 10)   # tier 0
        add_inventory_item(conn, user_id, "Truta", 4)       # tier 1

        await self._vender(conn, user_id, "tier1")

        self.assertEqual(get_wallet(conn, user_id), 60)
        inv = get_inventory(conn, user_id)
        self.assertEqual(inv.get("Sardinha", 0), 10, "tier 0 foi vendido junto")
        self.assertEqual(inv.get("Truta", 0), 0)

    async def test_sell_a_single_species(self):
        conn = self._make_conn()
        user_id = 9903
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 3)
        add_inventory_item(conn, user_id, "Lambari", 3)

        await self._vender(conn, user_id, "Sardinha")

        self.assertEqual(get_wallet(conn, user_id), 15)
        self.assertEqual(get_inventory(conn, user_id).get("Lambari", 0), 3)

    async def test_selling_never_touches_trash_or_gear(self):
        conn = self._make_conn()
        user_id = 9904
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 2)
        add_inventory_item(conn, user_id, "Bota Velha", 5)
        add_inventory_item(conn, user_id, "vara_ouro", 1)
        add_inventory_item(conn, user_id, "isca", 7)

        await self._vender(conn, user_id, "tudo")

        inv = get_inventory(conn, user_id)
        self.assertEqual(inv.get("Bota Velha", 0), 5, "lixo foi vendido")
        self.assertEqual(inv.get("vara_ouro", 0), 1, "vara foi vendida")
        self.assertEqual(inv.get("isca", 0), 7, "consumível foi vendido")
        self.assertEqual(get_wallet(conn, user_id), 10)

    async def test_trash_keyword_points_to_galdino_without_selling(self):
        conn = self._make_conn()
        user_id = 9905
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Bota Velha", 5)

        interaction = await self._vender(conn, user_id, "lixo")

        args, _ = interaction.response.send_message.call_args
        self.assertIn("Galdino", args[0])
        self.assertEqual(get_inventory(conn, user_id).get("Bota Velha", 0), 5)
        self.assertEqual(get_wallet(conn, user_id), 0)

    async def test_empty_selection_is_refused_without_charging(self):
        conn = self._make_conn()
        user_id = 9906
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 3)

        interaction = await self._vender(conn, user_id, "tier3")

        args, _ = interaction.response.send_message.call_args
        self.assertIn("não tem peixe", args[0])
        self.assertEqual(get_inventory(conn, user_id).get("Sardinha", 0), 3)

    async def test_unknown_term_is_refused(self):
        conn = self._make_conn()
        user_id = 9907
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 3)

        interaction = await self._vender(conn, user_id, "banana")

        args, _ = interaction.response.send_message.call_args
        self.assertIn("Não reconheço", args[0])
        self.assertEqual(get_inventory(conn, user_id).get("Sardinha", 0), 3)

    async def test_account_is_required(self):
        conn = self._make_conn()
        interaction = await self._vender(conn, 9908, "tudo")
        args, _ = interaction.response.send_message.call_args
        self.assertIn("/eco pescar", args[0])
        self.assertIsNone(
            conn.execute("SELECT 1 FROM users WHERE user_id = ?", (9908,)).fetchone()
        )

    async def test_selling_twice_does_not_pay_twice(self):
        conn = self._make_conn()
        user_id = 9909
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Arraia", 2)

        await self._vender(conn, user_id, "tudo")
        await self._vender(conn, user_id, "tudo")

        self.assertEqual(get_wallet(conn, user_id), 100)


class RedeDeArrastoTests(unittest.TestCase):
    """Dependência do Item 2: a Rede rendia menos que o Covo, que é grátis."""

    def _liquido_por_hora(self, key):
        st = economia.TRAP_TYPES[key]
        pool = [p for p in economia.FISH_DB if p[4] <= st["loot_tier_max"]]
        coletas_h = 3600 / (st["wait_time"] + st["reset_time"])
        bruto = coletas_h * st["capacity"] * (
            sum(economia.fish_sell_price(p[0]) for p in pool) / len(pool)
        )
        reparo = coletas_h * (st["break_chance"] / 100) * st["repair_cost"]
        return bruto - reparo

    def test_rede_stats_adjusted(self):
        rede = economia.TRAP_TYPES["rede_industrial"]
        self.assertEqual(rede["break_chance"], 35)
        self.assertEqual(rede["repair_cost"], 250)

    def test_rede_beats_the_free_covo(self):
        """O invariante que a mudança existe para garantir: um item pago não
        pode render menos que o gratuito."""
        covo = self._liquido_por_hora("covo_basico")
        rede = self._liquido_por_hora("rede_industrial")
        self.assertGreater(rede, covo)
        self.assertGreater(rede, covo * 2, "a Rede custa 1500; precisa compensar de verdade")

    def test_rede_pays_back_in_reasonable_time(self):
        ganho = self._liquido_por_hora("rede_industrial") - self._liquido_por_hora("covo_basico")
        self.assertLess(economia.TRAP_TYPES["rede_industrial"]["cost"] / ganho, 4.0)

    def test_no_trap_is_net_negative(self):
        for key in economia.TRAP_TYPES:
            with self.subTest(armadilha=key):
                self.assertGreater(self._liquido_por_hora(key), 0)


class ForjaDoAbismoTests(unittest.IsolatedAsyncioTestCase):
    """Item 3: escada sem teto de fim de jogo.

    O catálogo permanente do jogo soma 695.050 Sachês e a renda de topo passa
    de 1,6 milhão por HORA — qualquer sink de valor fixo é consumido no mesmo
    dia em que é lançado. O custo cresce geometricamente e o benefício
    linearmente; é essa assimetria que faz a escada sempre ganhar da renda que
    ela própria habilita.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _mestre(self, conn, user_id, wallet=10**9, scrap=10**6, rod="vara_void", rank="A"):
        """Jogador que cumpre os dois requisitos da forja."""
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "Tester"))
        conn.commit()
        ensure_user(conn, user_id, "Tester")
        set_current_rod(conn, user_id, rod)
        set_guild_rank(conn, user_id, rank, 0)
        modify_wallet(conn, user_id, wallet, "Tester")
        modify_scrap(conn, user_id, scrap)

    # ------------------------------------------------ custo
    def test_first_level_costs_the_base(self):
        self.assertEqual(forge_level_cost(1), {"saches": 50_000, "scrap": 500})

    def test_cost_follows_the_declared_geometric_curve(self):
        for n in (1, 5, 10, 20, 30, 40):
            with self.subTest(nivel=n):
                esperado = int(FORGE_BASE_COST * FORGE_GROWTH ** (n - 1))
                self.assertEqual(forge_level_cost(n)["saches"], esperado)

    def test_scrap_cost_is_linear(self):
        for n in (1, 7, 25):
            with self.subTest(nivel=n):
                self.assertEqual(forge_level_cost(n)["scrap"], 500 * n)

    def test_accumulated_cost_matches_the_published_table(self):
        """Os marcos que foram para a proposta aprovada. A tabela publicada
        arredondou, então a tolerância é relativa, não exata."""
        acumulado = 0
        marcos = {10: 2_040_000, 20: 31_200_000, 30: 433_500_000}
        for n in range(1, 31):
            acumulado += forge_level_cost(n)["saches"]
            if n in marcos:
                with self.subTest(nivel=n):
                    self.assertAlmostEqual(acumulado / marcos[n], 1.0, delta=0.05)

    def test_cost_grows_faster_than_the_bonus(self):
        """A propriedade que faz a escada nunca ser esgotada: custo geométrico
        contra benefício linear."""
        for n in range(1, 40):
            razao_custo = forge_level_cost(n + 1)["saches"] / forge_level_cost(n)["saches"]
            razao_bonus = forge_luck_multiplier(n + 1) / forge_luck_multiplier(n)
            with self.subTest(nivel=n):
                self.assertGreater(razao_custo, razao_bonus)

    def test_invalid_level_is_rejected(self):
        for n in (0, -1):
            with self.subTest(nivel=n):
                with self.assertRaises(ValueError):
                    forge_level_cost(n)

    # ------------------------------------------------ bônus
    def test_bonus_is_linear_not_compound(self):
        for nivel, esperado in [(0, 1.0), (1, 1.015), (10, 1.15), (20, 1.30), (30, 1.45)]:
            with self.subTest(nivel=nivel):
                self.assertAlmostEqual(forge_luck_multiplier(nivel), esperado, places=6)

    def test_bonus_floors_at_one_for_garbage_levels(self):
        self.assertEqual(forge_luck_multiplier(0), 1.0)
        self.assertEqual(forge_luck_multiplier(-5), 1.0)

    async def test_bonus_multiplies_rod_luck_and_upgrades_instead_of_replacing(self):
        """O ponto de integração: a forja é mais um fator na cadeia, não um
        substituto da sorte da vara nem dos upgrades do Galdino."""
        conn = self._make_conn()
        user_id = 8801
        self._mestre(conn, user_id, wallet=0, scrap=0)
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.execute("UPDATE rod_upgrades SET luck_level = 5 WHERE user_id = ?", (user_id,))
        conn.commit()

        async def pescar_uma(forge_level):
            set_forge_level(conn, user_id, forge_level)
            conn.execute("UPDATE user_cooldowns SET last_fish = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE users SET wallet = 0 WHERE user_id = ?", (user_id,))
            conn.commit()
            # Peixe e valor fixos: só o multiplicador da forja varia entre as
            # duas chamadas, então a razão dos ganhos é a razão dos bônus.
            alvo = next(p for p in economia.FISH_DB if p[0] == "Truta")
            with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                    patch.object(economia, "get_local_file", return_value=(None, None)), \
                    patch.object(economia.random, "choice", return_value=alvo), \
                    patch.object(economia.random, "randint", return_value=50), \
                    patch.object(economia, "get_current_weather",
                                 return_value=("normal", economia.WEATHER_EFFECTS["normal"])):
                await economia.pescar.callback(self._make_interaction(user_id))
            return get_wallet(conn, user_id)

        sem_forja = await pescar_uma(0)
        com_forja = await pescar_uma(20)

        # 50 (valor) x 6.6 (sorte da Devoradora) x 1.5 (upgrades luck 5) = 495
        self.assertEqual(sem_forja, 495)
        # ... x 1.30 (forja 20) = 643
        self.assertEqual(com_forja, 643)
        self.assertAlmostEqual(com_forja / sem_forja, 1.30, delta=0.01)

    # ------------------------------------------------ persistência
    def test_forge_level_defaults_to_zero_and_round_trips(self):
        conn = self._make_conn()
        user_id = 8802
        self.assertEqual(get_forge_level(conn, user_id), 0)
        set_forge_level(conn, user_id, 7)
        self.assertEqual(get_forge_level(conn, user_id), 7)
        self.assertEqual(get_rod_upgrades(conn, user_id)["forge"], 7)

    def test_forge_level_never_goes_negative(self):
        conn = self._make_conn()
        user_id = 8803
        set_forge_level(conn, user_id, -3)
        self.assertEqual(get_forge_level(conn, user_id), 0)

    def test_forge_level_survives_a_following_v4_command(self):
        """Mesmo contrato de set_guild_rank: o sync do comando seguinte não
        pode reverter a gravação."""
        conn = self._make_conn()
        user_id = 8804
        self._mestre(conn, user_id)
        set_forge_level(conn, user_id, 4)
        sync_user_to_economy(conn, user_id)
        modify_wallet(conn, user_id, 1, "Tester")
        self.assertEqual(get_forge_level(conn, user_id), 4)

    # ------------------------------------------------ desbloqueio
    def test_locked_without_a_tier5_rod(self):
        conn = self._make_conn()
        user_id = 8805
        self._mestre(conn, user_id, rod="vara_iridium")
        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "locked_rod")
        self.assertEqual(get_forge_level(conn, user_id), 0)

    def test_locked_without_rank_a(self):
        conn = self._make_conn()
        user_id = 8806
        self._mestre(conn, user_id, rank="B")
        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "locked_rank")
        self.assertEqual(get_forge_level(conn, user_id), 0)

    def test_both_tier5_rods_unlock(self):
        for rod in ("vara_quantum", "vara_void"):
            with self.subTest(vara=rod):
                conn = self._make_conn()
                user_id = 8807
                self._mestre(conn, user_id, rod=rod)
                self.assertTrue(try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)["success"])

    def test_allowed_rods_is_derived_from_rod_stats(self):
        esperado = {k for k, v in economia.ROD_STATS.items() if v["tier"] >= 5}
        self.assertEqual(set(economia.FORGE_ALLOWED_RODS), esperado)
        self.assertTrue(esperado)

    def test_gate_is_rechecked_inside_the_transaction(self):
        """A view fica aberta 180s; trocar de vara depois de abri-la não pode
        deixar a compra passar."""
        conn = self._make_conn()
        user_id = 8808
        self._mestre(conn, user_id, rod="vara_void")
        set_current_rod(conn, user_id, "vara_bambu")
        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "locked_rod")

    # ------------------------------------------------ compra
    def test_purchase_debits_both_currencies_and_raises_the_level(self):
        conn = self._make_conn()
        user_id = 8809
        self._mestre(conn, user_id, wallet=60_000, scrap=1_000)

        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)

        self.assertTrue(r["success"])
        self.assertEqual(r["level"], 1)
        self.assertEqual(get_wallet(conn, user_id), 10_000)
        self.assertEqual(get_scrap(conn, user_id), 500)
        self.assertEqual(get_forge_level(conn, user_id), 1)

    def test_purchase_is_refused_without_enough_saches(self):
        conn = self._make_conn()
        user_id = 8810
        self._mestre(conn, user_id, wallet=49_999, scrap=10_000)
        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
        self.assertEqual(r["reason"], "insufficient_saches")
        self.assertEqual(get_wallet(conn, user_id), 49_999)
        self.assertEqual(get_scrap(conn, user_id), 10_000)

    def test_purchase_is_refused_without_enough_scrap(self):
        conn = self._make_conn()
        user_id = 8811
        self._mestre(conn, user_id, wallet=10**7, scrap=499)
        r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
        self.assertEqual(r["reason"], "insufficient_scrap")
        self.assertEqual(get_forge_level(conn, user_id), 0)
        self.assertEqual(get_wallet(conn, user_id), 10**7, "cobrou Sachê numa compra recusada")

    def test_each_purchase_buys_exactly_one_level_at_the_current_price(self):
        conn = self._make_conn()
        user_id = 8812
        self._mestre(conn, user_id)
        for esperado in (1, 2, 3, 4, 5):
            antes = get_wallet(conn, user_id)
            r = try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)
            with self.subTest(nivel=esperado):
                self.assertEqual(r["level"], esperado)
                self.assertEqual(antes - get_wallet(conn, user_id), forge_level_cost(esperado)["saches"])

    def test_ladder_has_no_ceiling(self):
        """Sem teto de nível: o único freio é o preço."""
        conn = self._make_conn()
        user_id = 8813
        self._mestre(conn, user_id, wallet=10**12, scrap=10**7)
        for _ in range(30):
            self.assertTrue(try_upgrade_forge(conn, user_id, economia.FORGE_ALLOWED_RODS)["success"])
        self.assertEqual(get_forge_level(conn, user_id), 30)

    # ------------------------------------------------ UI
    async def test_locked_player_sees_the_requirements(self):
        conn = self._make_conn()
        user_id = 8814
        self._mestre(conn, user_id, rod="vara_bambu", rank="F")

        view = economia.GaldinoView(user_id, "Tester")
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.forge_btn.callback(interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        nomes = [f.name for f in embed.fields]
        self.assertTrue(any("Tier 5" in n for n in nomes))
        self.assertTrue(any(f"Rank {FORGE_REQUIRED_RANK}" in n for n in nomes))
        self.assertTrue(all(n.startswith("❌") for n in nomes), nomes)
        self.assertNotIn("view", interaction.response.send_message.call_args.kwargs)

    async def test_unlocked_player_gets_the_forge_panel(self):
        conn = self._make_conn()
        user_id = 8815
        self._mestre(conn, user_id)

        view = economia.GaldinoView(user_id, "Tester")
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.forge_btn.callback(interaction)

        kwargs = interaction.response.send_message.call_args.kwargs
        self.assertIsInstance(kwargs["view"], economia.ForgeView)
        self.assertIn("Nível 0", kwargs["embed"].title)

    async def test_forging_from_the_panel_raises_the_level(self):
        conn = self._make_conn()
        user_id = 8816
        self._mestre(conn, user_id)

        view = economia.ForgeView(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.forjar.callback(self._make_interaction(user_id))

        self.assertEqual(get_forge_level(conn, user_id), 1)

    async def test_another_player_cannot_use_the_panel(self):
        conn = self._make_conn()
        dono, intruso = 8817, 8818
        self._mestre(conn, dono)
        self._mestre(conn, intruso, wallet=0, scrap=0)

        view = economia.ForgeView(dono)
        interaction = self._make_interaction(intruso)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.forjar.callback(interaction)

        self.assertEqual(get_forge_level(conn, dono), 0)
        self.assertIn("não é sua", interaction.response.send_message.call_args.args[0])


class ForjaDrenagemTests(unittest.TestCase):
    """A razão 1,30 foi escolhida por simulação: a escada precisa drenar a
    maior parte da renda de 400 dias nos três perfis de jogador."""

    def _simular(self, lances_dia, explores, horas_trap, com_forja):
        from cogs.ilha import ISLAND_STRUCTURES, island_bonuses

        climas = {"normal": (1.0, 1.0, 0, 0.7), "bad": (0.5, 2.0, 0, 0.2), "good": (1.5, 0.5, 1, 0.1)}
        media = lambda p: (p[1] + p[2]) / 2
        iniciais = [p for p in economia.FISH_DB if p[4] == 0 and p[0] not in economia.TRASH_ITEMS]
        caminho = [
            "vara_bambu", "vara_treino", "vara_plastico", "vara_fibra", "vara_pesada",
            "vara_veloz", "vara_ouro", "vara_sonar", "vara_sortuda", "vara_iridium",
            "vara_magnetica", "vara_sniper", "vara_quantum", "vara_void",
        ]
        custo_ilha = sum(
            ISLAND_STRUCTURES["nucleo"]["cost_saches_per_level"] * n for n in range(1, 5)
        ) + sum(ISLAND_STRUCTURES[k]["cost_saches"] for k in ("deposito", "oficina", "farol"))
        bonus = island_bonuses({
            k: {"level": (4 if k == "nucleo" else 1), "status": "idle", "timer_end": None, "state_json": "{}"}
            for k in ISLAND_STRUCTURES
        })

        def ev(rod, ilha, forja):
            r = economia.ROD_STATS[rod]
            total = 0.0
            for _, (lm, tm, tb, peso) in climas.items():
                pt = max(0.0, min(100.0, r["trash"] * tm)) / 100.0
                mult = (
                    r["luck"] * (1 + (bonus["sorte_bonus"] if ilha else 0.0))
                    * forge_luck_multiplier(forja) * lm
                )
                parcial = pt * (1 - economia.TRASH_ROLL_RATIO) * (
                    sum(map(media, iniciais)) / len(iniciais)
                ) * mult
                pool = [p for p in economia.FISH_DB if 0 < p[4] <= r["tier"] + tb] or iniciais
                parcial += (1 - pt) * (
                    sum(media(p) for p in pool if p[0] not in economia.TRASH_ITEMS) / len(pool)
                ) * mult
                total += peso * parcial
            return total

        def trap(key):
            st = economia.TRAP_TYPES[key]
            pool = [p for p in economia.FISH_DB if p[4] <= st["loot_tier_max"]]
            ch = 3600 / (st["wait_time"] + st["reset_time"])
            bruto = ch * st["capacity"] * (sum(economia.fish_sell_price(p[0]) for p in pool) / len(pool))
            return bruto - ch * (st["break_chance"] / 100) * st["repair_cost"]

        covo, rede = trap("covo_basico"), trap("rede_industrial")
        wallet = renda = sink = 0.0
        rod = forja = 0
        ilha = False
        for dia in range(1, 401):
            cd = int(300 * economia.ROD_STATS[caminho[rod]]["cd"] * (1 - (bonus["cd_reducao"] if ilha else 0.0)))
            n = min(lances_dia, int(24 * 3600 / cd))
            ganho = ev(caminho[rod], ilha, forja) * n + 200 + min(dia, 60) * 50 + explores * 93.9
            if horas_trap:
                ganho += horas_trap * (rede if rod >= 10 else covo)
            wallet += ganho
            renda += ganho
            while rod + 1 < len(caminho) and wallet >= economia.ROD_STATS[caminho[rod + 1]]["price"]:
                wallet -= economia.ROD_STATS[caminho[rod + 1]]["price"]
                sink += economia.ROD_STATS[caminho[rod + 1]]["price"]
                rod += 1
            if not ilha and wallet >= custo_ilha:
                wallet -= custo_ilha
                sink += custo_ilha
                ilha = True
            if com_forja and economia.ROD_STATS[caminho[rod]]["tier"] >= 5:
                while wallet >= forge_level_cost(forja + 1)["saches"]:
                    forja += 1
                    wallet -= forge_level_cost(forja)["saches"]
                    sink += forge_level_cost(forja)["saches"]
        return {"wallet": wallet, "sink": sink, "renda": renda, "forja": forja}

    PERFIS = [(8, 0, 0, "casual"), (30, 3, 2, "ativo"), (96, 3, 6, "hardcore")]

    def test_without_the_forge_almost_nothing_is_drained(self):
        """A contraprova: sem a forja o catálogo inteiro é ruído."""
        for lances, expl, ht, nome in self.PERFIS:
            r = self._simular(lances, expl, ht, com_forja=False)
            with self.subTest(perfil=nome):
                self.assertLess(r["sink"] / r["renda"], 0.01)

    def test_the_forge_drains_the_bulk_of_the_income(self):
        for lances, expl, ht, nome in self.PERFIS:
            r = self._simular(lances, expl, ht, com_forja=True)
            with self.subTest(perfil=nome):
                self.assertGreater(r["sink"] / r["renda"], 0.80)

    def test_leftover_balance_is_savings_toward_the_next_level(self):
        """O saldo parado no dia 400 não é dinheiro sem destino: é menos do
        que custa o próximo nível, ou seja, poupança a caminho dele."""
        for lances, expl, ht, nome in self.PERFIS:
            r = self._simular(lances, expl, ht, com_forja=True)
            with self.subTest(perfil=nome):
                self.assertLess(r["wallet"], forge_level_cost(r["forja"] + 1)["saches"])


class TetoDeMissoesTests(unittest.IsolatedAsyncioTestCase):
    """Item 7: a missão era infinitamente repetível.

    O bloco de conclusão zerava active_mission_id/mission_progress e não
    registrava nada, então bastava reaceitar a mesma missão no quadro. No rank
    A isso são 8.000 Sachês por ciclo, sem limite.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _seed(self, conn, user_id):
        conn.execute(
            "INSERT INTO economy (user_id, user_name, guild_rank, guild_xp) VALUES (?, ?, 'F', 0)",
            (user_id, "Tester"),
        )
        conn.execute(
            "INSERT INTO quest_progress (user_id, current_chapter) VALUES (?, 'acesso_liberado')",
            (user_id,),
        )
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    def _armar(self, conn, user_id, mission_id="f1", progress=4, target=5):
        """Party de um membro com a missão a uma pescaria de fechar."""
        conn.execute(
            "INSERT OR REPLACE INTO parties (leader_id, leader_name, members_json, "
            "active_mission_id, mission_progress, mission_target) VALUES (?, ?, '[]', ?, ?, ?)",
            (user_id, "Tester", mission_id, progress, target),
        )
        conn.commit()

    async def _pescar(self, conn, user_id):
        conn.execute("UPDATE user_cooldowns SET last_fish = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "get_local_file", return_value=(None, None)):
            await economia.pescar.callback(interaction)
        return interaction

    # ------------------------------------------------ camada de dados
    def test_first_registration_succeeds_and_second_fails(self):
        conn = self._make_conn()
        primeira = try_register_mission_completion(conn, 100, "a1")
        self.assertTrue(primeira["success"])
        self.assertEqual(primeira["restantes"], MISSION_DAILY_CAP - 1)

        segunda = try_register_mission_completion(conn, 100, "a1")
        self.assertFalse(segunda["success"])
        self.assertEqual(segunda["reason"], "already_today")

    def test_daily_cap_blocks_the_fourth_distinct_mission(self):
        conn = self._make_conn()
        for i in range(MISSION_DAILY_CAP):
            self.assertTrue(try_register_mission_completion(conn, 101, f"m{i}")["success"])

        excedente = try_register_mission_completion(conn, 101, "m_extra")
        self.assertFalse(excedente["success"])
        self.assertEqual(excedente["reason"], "daily_cap")
        self.assertEqual(mission_slots_left(conn, 101), 0)

    def test_cap_is_per_party_not_global(self):
        conn = self._make_conn()
        for i in range(MISSION_DAILY_CAP):
            try_register_mission_completion(conn, 102, f"m{i}")
        self.assertEqual(mission_slots_left(conn, 102), 0)
        self.assertEqual(mission_slots_left(conn, 103), MISSION_DAILY_CAP)
        self.assertTrue(try_register_mission_completion(conn, 103, "m0")["success"])

    def test_yesterdays_completions_do_not_count(self):
        """O reset é na virada do dia civil, mesmo critério do /eco diario."""
        conn = self._make_conn()
        ontem = (datetime.now().date() - timedelta(days=1)).isoformat()
        for i in range(MISSION_DAILY_CAP):
            conn.execute(
                "INSERT INTO mission_completions (leader_id, mission_id, completed_on) VALUES (?, ?, ?)",
                (104, f"m{i}", ontem),
            )
        conn.commit()

        self.assertEqual(missions_completed_today(conn, 104), set())
        self.assertEqual(mission_slots_left(conn, 104), MISSION_DAILY_CAP)
        self.assertTrue(try_register_mission_completion(conn, 104, "m0")["success"])

    def test_completed_set_reflects_only_today(self):
        conn = self._make_conn()
        try_register_mission_completion(conn, 105, "b1")
        try_register_mission_completion(conn, 105, "b2")
        self.assertEqual(missions_completed_today(conn, 105), {"b1", "b2"})
        self.assertEqual(mission_slots_left(conn, 105), MISSION_DAILY_CAP - 2)

    # ------------------------------------------------ caminho de /eco pescar
    async def test_mission_does_not_pay_twice_in_the_same_day(self):
        conn = self._make_conn()
        user_id = 9991
        self._seed(conn, user_id)

        self._armar(conn, user_id)
        await self._pescar(conn, user_id)
        saldo_apos_primeira = get_wallet(conn, user_id)
        self.assertGreater(saldo_apos_primeira, 0)

        # Reaceita a MESMA missão e fecha de novo — era o loop infinito.
        self._armar(conn, user_id)
        await self._pescar(conn, user_id)

        recompensa = 50  # prêmio da f1
        self.assertLess(
            get_wallet(conn, user_id) - saldo_apos_primeira,
            recompensa,
            "a missão pagou duas vezes no mesmo dia",
        )

    async def test_reward_is_paid_on_the_first_completion(self):
        conn = self._make_conn()
        user_id = 9992
        self._seed(conn, user_id)
        self._armar(conn, user_id)

        interaction = await self._pescar(conn, user_id)

        self.assertEqual(missions_completed_today(conn, user_id), {"f1"})
        embed = interaction.followup.send.call_args.kwargs["embed"]
        self.assertIn("MISSÃO CUMPRIDA", embed.description)

    async def test_blocked_completion_explains_itself(self):
        conn = self._make_conn()
        user_id = 9993
        self._seed(conn, user_id)
        try_register_mission_completion(conn, user_id, "f1")

        self._armar(conn, user_id)
        interaction = await self._pescar(conn, user_id)

        embed = interaction.followup.send.call_args.kwargs["embed"]
        self.assertIn("já tinha sido concluída hoje", embed.description)

    async def test_daily_cap_message_when_exhausted(self):
        conn = self._make_conn()
        user_id = 9994
        self._seed(conn, user_id)
        for i in range(MISSION_DAILY_CAP):
            try_register_mission_completion(conn, user_id, f"outra{i}")

        self._armar(conn, user_id)
        interaction = await self._pescar(conn, user_id)

        embed = interaction.followup.send.call_args.kwargs["embed"]
        self.assertIn("teto de", embed.description)

    # ------------------------------------------------ MissionSelect
    def test_select_hides_missions_already_done_today(self):
        select = economia.MissionSelect(9995, "F", feitas_hoje=set(), vagas=3)
        ids = {o.value for o in select.options}
        self.assertTrue(ids)

        alvo = next(iter(ids))
        filtrado = economia.MissionSelect(9995, "F", feitas_hoje={alvo}, vagas=3)
        self.assertNotIn(alvo, {o.value for o in filtrado.options})

    def test_select_is_disabled_when_the_cap_is_reached(self):
        select = economia.MissionSelect(9996, "F", feitas_hoje=set(), vagas=0)
        self.assertTrue(select.disabled)
        self.assertEqual([o.value for o in select.options], ["__indisponivel__"])

    def test_select_never_ships_an_empty_option_list(self):
        """O Discord recusa um Select sem opções — o estado 'nada disponível'
        precisa de um placeholder, senão o quadro inteiro quebra."""
        todas = {m["id"] for m in economia.MISSION_DB["F"]}
        select = economia.MissionSelect(9997, "F", feitas_hoje=todas, vagas=3)
        self.assertEqual(len(select.options), 1)
        self.assertEqual(select.options[0].value, "__indisponivel__")

    async def test_select_refuses_the_placeholder(self):
        conn = self._make_conn()
        select = economia.MissionSelect(9998, "F", feitas_hoje=set(), vagas=0)
        select._values = ["__indisponivel__"]
        interaction = self._make_interaction(9998)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)
        self.assertIn("Nenhuma missão disponível", interaction.response.send_message.call_args.args[0])

    async def test_select_rechecks_the_cap_at_click_time(self):
        """A view fica aberta; outro membro pode fechar uma missão no meio."""
        conn = self._make_conn()
        user_id = 9999
        self._seed(conn, user_id)
        select = economia.MissionSelect(user_id, "F", feitas_hoje=set(), vagas=3)
        select._values = [economia.MISSION_DB["F"][0]["id"]]

        for i in range(MISSION_DAILY_CAP):
            try_register_mission_completion(conn, user_id, f"depois{i}")

        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await select.callback(interaction)

        self.assertIn("já concluiu", interaction.response.send_message.call_args.args[0])
        party = conn.execute(
            "SELECT active_mission_id FROM parties WHERE leader_id = ?", (user_id,)
        ).fetchone()
        self.assertIsNone(party["active_mission_id"] if party else None)


class CaixaMisteriosaTests(unittest.IsolatedAsyncioTestCase):
    """Item 6: a caixa tinha EV POSITIVO para o jogador.

    500 de custo pagando randint(100, 1000) dá EV 550 — comprar em lote era
    renda, não gasto, e o modal de compra aceita 4 dígitos de quantidade.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name="Tester", display_name="Tester"),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _valor_de_referencia(self, resultado):
        if resultado["tipo"] == "item":
            return economia.SHOP_ITEMS[resultado["item"]]["price"]
        return resultado["valor"]

    # ------------------------------------------------ distribuição
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(f[0] for f in economia.CAIXA_FAIXAS), 1.0)

    def test_expected_value_is_below_the_price(self):
        """A propriedade que estava errada: um sink não pode pagar mais do que
        custa. Amostragem grande com semente fixa — determinística."""
        rng = random.Random(20260823)
        n = 300_000
        total = sum(self._valor_de_referencia(economia.abrir_caixa_misteriosa(rng)) for _ in range(n))
        ev = total / n
        preco = economia.SHOP_ITEMS["caixa_misteriosa"]["price"]

        self.assertLess(ev, preco, "a Caixa Misteriosa voltou a ser lucrativa")
        self.assertAlmostEqual(ev / preco - 1, -0.098, delta=0.015)

    def test_distribution_matches_the_declared_weights(self):
        rng = random.Random(4242)
        n = 200_000
        contagem = Counter(economia.abrir_caixa_misteriosa(rng)["tipo"] for _ in range(n))
        self.assertAlmostEqual(contagem["item"] / n, 0.080, delta=0.005)
        self.assertAlmostEqual(contagem["jackpot"] / n, 0.025, delta=0.005)
        self.assertAlmostEqual(contagem["dinheiro"] / n, 0.895, delta=0.008)

    def test_every_outcome_stays_in_its_declared_range(self):
        rng = random.Random(7)
        for _ in range(20_000):
            r = economia.abrir_caixa_misteriosa(rng)
            if r["tipo"] == "item":
                self.assertIn(r["item"], economia.CAIXA_PREMIO_ITENS)
                self.assertEqual(r["valor"], 0)
            elif r["tipo"] == "jackpot":
                self.assertGreaterEqual(r["valor"], 3000)
                self.assertLessEqual(r["valor"], 6000)
            else:
                self.assertGreaterEqual(r["valor"], 50)
                self.assertLessEqual(r["valor"], 900)

    def test_prize_pool_items_all_exist_in_the_shop(self):
        for chave in economia.CAIXA_PREMIO_ITENS:
            with self.subTest(item=chave):
                self.assertIn(chave, economia.SHOP_ITEMS)
                self.assertGreater(economia.SHOP_ITEMS[chave]["price"], 0)

    def test_price_unchanged(self):
        self.assertEqual(economia.SHOP_ITEMS["caixa_misteriosa"]["price"], 500)

    # ------------------------------------------------ uso real
    async def test_opening_consumes_the_box_and_pays_money(self):
        conn = self._make_conn()
        user_id = 9981
        ensure_user(conn, user_id, "Tester")
        add_inventory_item(conn, user_id, "caixa_misteriosa", 2)

        select = economia.ConsumeSelect(user_id, {"caixa_misteriosa": 2})
        select._values = ["caixa_misteriosa"]
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "abrir_caixa_misteriosa",
                             return_value={"tipo": "dinheiro", "valor": 321, "item": None}):
            await select.callback(self._make_interaction(user_id))

        self.assertEqual(get_inventory(conn, user_id).get("caixa_misteriosa", 0), 1)
        self.assertEqual(get_wallet(conn, user_id), 321)

    async def test_opening_can_deliver_an_item_instead_of_money(self):
        conn = self._make_conn()
        user_id = 9982
        ensure_user(conn, user_id, "Tester")
        add_inventory_item(conn, user_id, "caixa_misteriosa", 1)

        select = economia.ConsumeSelect(user_id, {"caixa_misteriosa": 1})
        select._values = ["caixa_misteriosa"]
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
                patch.object(economia, "abrir_caixa_misteriosa",
                             return_value={"tipo": "item", "valor": 0, "item": "firewall"}):
            interaction = self._make_interaction(user_id)
            await select.callback(interaction)

        self.assertEqual(get_inventory(conn, user_id).get("firewall", 0), 1)
        self.assertEqual(get_wallet(conn, user_id), 0)
        self.assertIn("Firewall", interaction.response.send_message.call_args.args[0])


class SucataTests(unittest.IsolatedAsyncioTestCase):
    """Item 5: a sucata só vinha de lixo, e as varas boas evitam lixo.

    Sniper .50 e Devoradora têm 0% de lixo e não produziam nenhuma sucata,
    para sempre — enquanto os upgrades pagos em sucata são o melhor retorno
    por unidade de recurso do jogo.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name, display_name=name),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def _conta(self, conn, user_id):
        conn.execute("INSERT INTO economy (user_id, user_name) VALUES (?, ?)", (user_id, "Tester"))
        conn.commit()
        ensure_user(conn, user_id, "Tester")

    def _com_bau(self, conn, user_id):
        conn.execute(
            "INSERT INTO user_island_structures (user_id, structure_key, level, status) "
            "VALUES (?, 'deposito', 1, 'idle')",
            (user_id,),
        )
        conn.commit()

    # ------------------------------------------------ tabela de rendimento
    def test_scrap_per_fish_table(self):
        for nome, (tier, _, _) in economia.FISH_BY_NAME.items():
            with self.subTest(peixe=nome):
                self.assertEqual(economia.fish_scrap_yield(nome), economia.SCRAP_PER_FISH[tier])

    def test_trash_yields_no_scrap_through_the_sell_path(self):
        """Lixo tem o caminho dele (Galdino); não pode contar duas vezes."""
        for t in economia.TRASH_ITEMS:
            with self.subTest(item=t):
                self.assertEqual(economia.fish_scrap_yield(t), 0)

    def test_desmanche_yield_formula(self):
        for tier, esperado in [(0, 2), (1, 6), (2, 10), (3, 14), (4, 18)]:
            with self.subTest(tier=tier):
                self.assertEqual(economia.desmanche_yield(tier), esperado)

    # ------------------------------------------------ grant_scrap
    def test_grant_scrap_applies_the_island_multiplier(self):
        conn = self._make_conn()
        user_id = 9951
        self._conta(conn, user_id)

        self.assertEqual(economia.grant_scrap(conn, user_id, 100), 100)
        self._com_bau(conn, user_id)
        self.assertEqual(economia.grant_scrap(conn, user_id, 100), 125)
        self.assertEqual(get_scrap(conn, user_id), 225)

    def test_grant_scrap_ignores_non_positive(self):
        conn = self._make_conn()
        user_id = 9952
        self._conta(conn, user_id)
        self.assertEqual(economia.grant_scrap(conn, user_id, 0), 0)
        self.assertEqual(economia.grant_scrap(conn, user_id, -50), 0)
        self.assertEqual(get_scrap(conn, user_id), 0)

    # ------------------------------------------------ /vender dá sucata
    async def test_selling_grants_scrap_as_a_byproduct(self):
        conn = self._make_conn()
        user_id = 9953
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Sardinha", 4)   # tier 0 -> 1 cada
        add_inventory_item(conn, user_id, "Arraia", 2)     # tier 2 -> 3 cada

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.vender.callback(self._make_interaction(user_id), "tudo")

        self.assertEqual(get_scrap(conn, user_id), 4 * 1 + 2 * 3)

    async def test_selling_scrap_respects_the_bau(self):
        conn = self._make_conn()
        user_id = 9954
        self._conta(conn, user_id)
        self._com_bau(conn, user_id)
        add_inventory_item(conn, user_id, "Arraia", 4)     # 4 x 3 = 12 -> x1.25 = 15

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.vender.callback(self._make_interaction(user_id), "tudo")

        self.assertEqual(get_scrap(conn, user_id), 15)

    # ------------------------------------------------ recicladora do Galdino
    async def test_galdino_recycle_respects_the_bau(self):
        conn = self._make_conn()
        user_id = 9955
        self._conta(conn, user_id)
        add_inventory_item(conn, user_id, "Bota Velha", 4)  # 4 x 5 = 20

        view = economia.GaldinoView(user_id, "Tester")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.recycle_btn.callback(self._make_interaction(user_id))
        self.assertEqual(get_scrap(conn, user_id), 20)

        user_id2 = 9956
        self._conta(conn, user_id2)
        self._com_bau(conn, user_id2)
        add_inventory_item(conn, user_id2, "Bota Velha", 4)
        view2 = economia.GaldinoView(user_id2, "Tester")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view2.recycle_btn.callback(self._make_interaction(user_id2))
        self.assertEqual(get_scrap(conn, user_id2), 25)

    # ------------------------------------------------ desmanche
    async def test_desmanchar_swaps_sache_for_scrap(self):
        conn = self._make_conn()
        user_id = 9957
        self._conta(conn, user_id)
        modify_wallet(conn, user_id, 500, "Tester")

        view = economia.DesmancharView(user_id, "Truta", 1, 120)
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.desmanchar.callback(interaction)

        self.assertEqual(get_wallet(conn, user_id), 380, "o Sachê da captura não foi estornado")
        self.assertEqual(get_scrap(conn, user_id), economia.desmanche_yield(1))

    async def test_desmanchar_cannot_be_used_twice(self):
        conn = self._make_conn()
        user_id = 9958
        self._conta(conn, user_id)
        modify_wallet(conn, user_id, 500, "Tester")

        view = economia.DesmancharView(user_id, "Truta", 1, 100)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.desmanchar.callback(self._make_interaction(user_id))
            saldo = get_wallet(conn, user_id)
            sucata = get_scrap(conn, user_id)
            segunda = self._make_interaction(user_id)
            await view.desmanchar.callback(segunda)

        self.assertEqual(get_wallet(conn, user_id), saldo)
        self.assertEqual(get_scrap(conn, user_id), sucata)
        self.assertIn("já foi desmanchada", segunda.response.send_message.call_args.args[0])

    async def test_desmanchar_is_refused_for_other_players(self):
        conn = self._make_conn()
        dono, intruso = 9959, 9960
        self._conta(conn, dono)
        self._conta(conn, intruso)
        modify_wallet(conn, dono, 500, "Tester")

        view = economia.DesmancharView(dono, "Truta", 1, 100)
        interaction = self._make_interaction(intruso)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.desmanchar.callback(interaction)

        self.assertEqual(get_wallet(conn, dono), 500)
        self.assertEqual(get_scrap(conn, dono), 0)
        self.assertIn("não é sua", interaction.response.send_message.call_args.args[0])

    async def test_zero_trash_rods_can_finally_produce_scrap(self):
        """O ponto do item: a Devoradora tem 0% de lixo e não tinha NENHUMA
        fonte de sucata na pescaria."""
        conn = self._make_conn()
        user_id = 9961
        self._conta(conn, user_id)
        self.assertEqual(economia.ROD_STATS["vara_void"]["trash"], 0)

        modify_wallet(conn, user_id, 50000, "Tester")
        view = economia.DesmancharView(user_id, "CTHULHU", 4, 50000)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.desmanchar.callback(self._make_interaction(user_id))

        self.assertEqual(get_scrap(conn, user_id), economia.desmanche_yield(4))
        self.assertGreater(get_scrap(conn, user_id), 0)


class IscaDiariaTests(unittest.IsolatedAsyncioTestCase):
    """Item 4: a Bancada do Náufrago entrega 1 Isca Minhoca por dia.

    Entregue dentro de /eco diario porque ele já É o portão diário do jogo —
    um segundo cooldown para a mesma cadência seria estado duplicado.
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name="Tester", display_name="Tester"),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def _diario(self, conn, user_id):
        interaction = self._make_interaction(user_id)
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.diario.callback(interaction)
        return interaction

    def _com_bancada(self, conn, user_id):
        conn.execute(
            "INSERT INTO user_island_structures (user_id, structure_key, level, status) "
            "VALUES (?, 'oficina', 1, 'idle')",
            (user_id,),
        )
        conn.commit()

    async def test_without_the_workshop_no_free_bait(self):
        conn = self._make_conn()
        user_id = 9971
        ensure_user(conn, user_id, "Tester")
        await self._diario(conn, user_id)
        self.assertEqual(get_inventory(conn, user_id).get("isca", 0), 0)

    async def test_with_the_workshop_one_bait_per_day(self):
        conn = self._make_conn()
        user_id = 9972
        ensure_user(conn, user_id, "Tester")
        self._com_bancada(conn, user_id)

        interaction = await self._diario(conn, user_id)

        self.assertEqual(get_inventory(conn, user_id).get("isca", 0), 1)
        self.assertIn("Bancada", interaction.response.send_message.call_args.args[0])

    async def test_bait_is_not_granted_twice_on_the_same_day(self):
        """A trava é a do próprio /eco diario — a isca herda o mesmo portão."""
        conn = self._make_conn()
        user_id = 9973
        ensure_user(conn, user_id, "Tester")
        self._com_bancada(conn, user_id)

        await self._diario(conn, user_id)
        await self._diario(conn, user_id)

        self.assertEqual(get_inventory(conn, user_id).get("isca", 0), 1)


if __name__ == "__main__":
    unittest.main()
