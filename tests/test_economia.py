import asyncio
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs import economia
from economy_db import (
    add_guild_xp,
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
    try_spend_wallet,
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
        # Rank E (req_xp 500) com 600 XP: promoção elegível para D, sobrando
        # 100 XP. Rank F não serve aqui porque seu req_xp é 0 — promoveria
        # com qualquer XP e não exercitaria a checagem.
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, guild_rank, guild_xp) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 100, "E", 600),
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
        """Cenário exato pedido no ticket: promoção E/600 -> D/100, seguida de
        recompensa de missão de grupo. Rank tem que continuar D e o XP tem que
        somar, nunca voltar para o estado pré-promoção."""
        conn = self._make_conn()
        user_id = 7402
        self._seed(conn, user_id, "E", 600)

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

        # D exige 1500 XP para subir, então 142 não promove de novo.
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
        self.assertEqual(get_rod_upgrades(conn, user_id), {"luck": 0, "cd": 0})
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

        with patch.object(economia.random, "randint", return_value=500):
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



if __name__ == "__main__":
    unittest.main()
