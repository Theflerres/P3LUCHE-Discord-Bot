import asyncio
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import economia
from economy_db import (
    add_inventory_item,
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_current_rod,
    get_inventory,
    get_rod_upgrades,
    get_scrap,
    get_wallet,
    modify_scrap,
    modify_wallet,
    set_cooldown,
    sync_user_to_economy,
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
    """Regressão para o fix de duplicação de saldo/inventário no QTE de pesca.

    Cenário: o snapshot (`inv_before`) é capturado no início de `/eco pescar`,
    mas o QTE (Tier 3+) deixa a escrita final pendente por até ~15-17s. Se,
    durante essa janela, outro comando (ex.: /eco comprar, já migrado pra v4)
    alterar saldo/inventário no banco, `_finalize_pescar` NÃO pode sobrescrever
    essa mudança com o snapshot antigo — precisa aplicar a pescaria como
    delta em cima do estado v4 fresco (via modify_wallet/add_inventory_item).
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(name=name),
            followup=SimpleNamespace(send=AsyncMock()),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

    async def test_external_purchase_during_qte_window_is_not_reverted(self):
        conn = self._make_conn()
        user_id = 42

        # Estado inicial, no momento em que /eco pescar lê o usuário.
        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 1000, "Tester")
        add_inventory_item(conn, user_id, "isca", 5)

        # Snapshot capturado no início de pescar(), antes do QTE abrir.
        inv_before = {"isca": 5}
        # Estado local após o consumo de 1 isca pela tentativa de pesca
        # (mutação em memória feita antes do QTE, como no código real).
        inv_after_local = {"isca": 4}

        # --- Ação externa durante a janela do QTE (ex: /eco comprar, v4) ---
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
            await economia._finalize_pescar(interaction, ctx, edit=False)

        # O gasto externo (300) continua valendo: saldo = 700 (pós-compra) + 300 (peixe).
        # Sem o fix, seria 1000 (saldo antigo do snapshot) + 300 = 1300, revertendo a compra.
        self.assertEqual(get_wallet(conn, user_id), 1000)
        final_inv = get_inventory(conn, user_id)
        # O item comprado durante a janela do QTE não pode desaparecer.
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

    async def test_timeout_path_also_preserves_external_mutation(self):
        conn = self._make_conn()
        user_id = 77

        ensure_user(conn, user_id, "Tester")
        modify_wallet(conn, user_id, 500, "Tester")
        add_inventory_item(conn, user_id, "isca", 2)

        inv_before = {"isca": 2}
        inv_after_local = {"isca": 1}

        # Ação externa durante a espera do QTE que acaba estourando o tempo.
        add_inventory_item(conn, user_id, "presente", 3)

        ctx = {
            "user_id": user_id,
            "interaction": self._make_interaction(),
            "inv": inv_after_local,
            "inv_before": inv_before,
            "qte_message": SimpleNamespace(edit=AsyncMock()),
            "agora_str": "2026-08-13 12:05:00.000000",
            "new_xp_total": 0,
            "current_rank": "F",
        }

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia._finalize_pescar_timeout(ctx)

        final_inv = get_inventory(conn, user_id)
        self.assertEqual(final_inv.get("presente"), 3)
        self.assertEqual(final_inv.get("isca"), 1)

        legacy = conn.execute(
            "SELECT inventory, last_fish FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        legacy_inv = json.loads(legacy["inventory"])
        self.assertEqual(legacy_inv.get("presente"), 3)
        self.assertEqual(legacy_inv.get("isca"), 1)
        self.assertEqual(legacy["last_fish"], "2026-08-13 12:05:00.000000")


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
    """/eco presentear migrado pra v4 (saldo/inventário), preservando a
    checagem de posse pra presentes grátis e o campo legado rod_tier (sem
    equivalente na v4).
    """

    def _make_conn(self):
        return _make_pescar_conn()

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_flex_gift_deducts_sender_and_grants_receiver(self):
        conn = self._make_conn()
        sender_id, receiver_id = 40, 41
        ensure_user(conn, sender_id, "Sender")
        modify_wallet(conn, sender_id, 6000, "Sender")

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "certificado")

        self.assertEqual(get_wallet(conn, sender_id), 6000 - 5000)
        self.assertEqual(get_inventory(conn, receiver_id).get("Certificado de Dono"), 1)
        self.assertIn("Enviado", interaction.response.send_message.call_args.args[0])

    async def test_insufficient_balance_refuses_without_charging(self):
        conn = self._make_conn()
        sender_id, receiver_id = 42, 43
        ensure_user(conn, sender_id, "Pobre")
        modify_wallet(conn, sender_id, 100, "Pobre")

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "certificado")

        self.assertEqual(get_wallet(conn, sender_id), 100)
        self.assertEqual(get_inventory(conn, receiver_id).get("Certificado de Dono", 0), 0)
        self.assertIn("Falta grana", interaction.response.send_message.call_args.args[0])

    async def test_rod_gift_blocked_when_receiver_already_has_better_rod(self):
        conn = self._make_conn()
        sender_id, receiver_id = 44, 45
        ensure_user(conn, sender_id, "Sender")
        modify_wallet(conn, sender_id, 6000, "Sender")
        ensure_user(conn, receiver_id, "Amigo")
        sync_user_to_economy(conn, receiver_id)
        conn.execute("UPDATE economy SET rod_tier = 5 WHERE user_id = ?", (receiver_id,))
        conn.commit()

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "vara_plastico")

        # Bloqueado ANTES de cobrar o remetente.
        self.assertEqual(get_wallet(conn, sender_id), 6000)
        self.assertIn("já tem vara melhor", interaction.response.send_message.call_args.args[0])

    async def test_rod_gift_success_equips_and_sets_legacy_rod_tier(self):
        conn = self._make_conn()
        sender_id, receiver_id = 46, 47
        ensure_user(conn, sender_id, "Sender")
        modify_wallet(conn, sender_id, 6000, "Sender")

        interaction = self._make_interaction(sender_id)
        amigo = SimpleNamespace(id=receiver_id, name="Amigo")
        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await economia.presentear.callback(interaction, amigo, "vara_plastico")

        self.assertEqual(get_wallet(conn, sender_id), 6000 - 600)
        self.assertEqual(get_current_rod(conn, receiver_id), "vara_plastico")
        legacy = conn.execute("SELECT rod_tier FROM economy WHERE user_id = ?", (receiver_id,)).fetchone()
        self.assertEqual(legacy["rod_tier"], 1)

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
        self.assertEqual(get_inventory(conn, receiver_id).get("Coroa do Imperador"), 1)

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
        self.assertEqual(get_inventory(conn, receiver_id).get("Coroa do Imperador", 0), 0)


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

    async def test_second_pescar_during_open_qte_window_is_rejected_by_cooldown(self):
        """Reproduz o cenário real: a 1ª chamada engata um peixe Tier 3+ e
        fica com o QTE aberto (suspensa em `await asyncio.sleep`, tal como
        aconteceria por até ~17s aguardando o clique do jogador). Enquanto
        ela está suspensa nesse ponto — ou seja, ANTES de `_finalize_pescar`
        rodar —, a 2ª chamada do mesmo usuário deve ser rejeitada pela
        checagem de cooldown, porque o cooldown já foi reservado no início
        da 1ª chamada, e não apenas no final (que só aconteceria quando o
        jogador resolvesse o QTE).
        """
        conn = self._make_conn()
        user_id = 999

        # Vara de tier alto + Firewall (zera a chance de lixo) garante que a
        # 1ª chamada sempre sorteia um peixe real; o patch de random.choice
        # abaixo garante que esse peixe seja especificamente Tier 3+, para
        # acionar o QTE de forma determinística (sem depender de sorte).
        # Inserida direto na tabela legada pra simular um usuário que já
        # existia antes desta migração — pescar() precisa sincronizar isso
        # pra v4 sozinho antes de ler.
        conn.execute(
            "INSERT INTO economy (user_id, user_name, wallet, current_rod, inventory) VALUES (?, ?, ?, ?, ?)",
            (user_id, "Tester", 1000, "vara_void", json.dumps({"firewall": 1})),
        )
        conn.commit()

        interaction1 = self._make_interaction(user_id)
        interaction2 = self._make_interaction(user_id)

        real_choice = economia.random.choice
        real_sleep = asyncio.sleep  # capturado ANTES de patchear: economia.asyncio
        # é o mesmo objeto módulo `asyncio` global, então patchear seu atributo
        # `sleep` afeta todo mundo (inclusive este helper) — sem capturar a
        # função real antes, o helper chamaria a si mesmo indefinidamente.

        def choose_tier3_plus(seq):
            candidates = [item for item in seq if isinstance(item, tuple) and len(item) == 6 and item[4] >= 3]
            return candidates[0] if candidates else real_choice(seq)

        async def instant_yield(*_args, **_kwargs):
            # Substitui o `await asyncio.sleep(2)` do QTE por um yield real
            # (cede o loop uma vez), só para o teste não esperar 2s de verdade
            # — continua sendo um ponto de suspensão genuíno para a 2ª
            # chamada interlear, exatamente como aconteceria na espera real.
            await real_sleep(0)

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(economia.random, "choice", side_effect=choose_tier3_plus), \
             patch.object(economia.asyncio, "sleep", side_effect=instant_yield):
            task1 = asyncio.create_task(economia.pescar.callback(interaction1))
            # Cede o loop uma vez: task1 roda sincronamente (mocks não geram
            # suspensão real) até o único ponto de suspensão genuína dentro
            # dela — o `asyncio.sleep` do QTE — e fica pendurada ali, com o
            # cooldown já reservado mas a pescaria ainda não finalizada.
            await asyncio.sleep(0)

            self.assertFalse(task1.done(), "esperava a 1ª chamada suspensa no QTE, não finalizada")

            # 2ª chamada do MESMO usuário enquanto a 1ª ainda está com o QTE aberto.
            await economia.pescar.callback(interaction2)

            await task1

        # 1ª chamada: chegou a abrir o QTE (mensagem de tensão), não terminou
        # com erro.
        self.assertTrue(interaction1.followup.send.called)
        call1_kwargs = interaction1.followup.send.call_args.kwargs
        self.assertIn("embed", call1_kwargs)
        # (o embed é reutilizado por referência e sua description é mutada
        # depois pelo próprio código ao editar a mensagem do QTE; o title
        # não é mutado, então é o campo estável para checar aqui.)
        self.assertIn("PEIXE FORTE", call1_kwargs["embed"].title or "")

        # 2ª chamada: rejeitada pela checagem normal de cooldown — não abriu
        # um segundo fluxo de captura em paralelo.
        self.assertTrue(interaction2.followup.send.called)
        call2_args, call2_kwargs = interaction2.followup.send.call_args
        rejection_text = call2_args[0] if call2_args else call2_kwargs.get("content", "")
        self.assertIn("Descansando", rejection_text)
        self.assertNotIn("embed", call2_kwargs)

        # O cooldown foi reservado pela 1ª chamada (last_fish já gravado),
        # mesmo com a pescaria dela ainda não finalizada (fish_count == 0,
        # pois o QTE nunca foi resolvido neste teste). Checa tanto a v4
        # quanto a tabela legada (têm que estar em sincronia).
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

        with patch.object(economia, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
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


if __name__ == "__main__":
    unittest.main()
