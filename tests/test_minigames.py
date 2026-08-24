import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import minigames
from economy_db import (
    add_inventory_item,
    ensure_user,
    ensure_v4_tables,
    get_cooldowns,
    get_inventory,
    get_wallet,
    modify_wallet,
    set_cooldown,
)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_v4_tables(conn)
    return conn


class MinigamesTests(unittest.TestCase):
    def test_auction_min_bid_scales_with_rarity(self):
        common = {"price": 100, "rarity": "common"}
        rare = {"price": 100, "rarity": "rare"}
        epic = {"price": 100, "rarity": "epic"}
        legendary = {"price": 100, "rarity": "legendary"}
        mythic = {"price": 100, "rarity": "mythic"}

        self.assertEqual(minigames._get_auction_min_bid(common), 50)
        self.assertEqual(minigames._get_auction_min_bid(rare), 75)
        self.assertEqual(minigames._get_auction_min_bid(epic), 100)
        self.assertEqual(minigames._get_auction_min_bid(legendary), 150)
        self.assertEqual(minigames._get_auction_min_bid(mythic), 200)

    def test_build_auction_start_message_includes_item_name(self):
        message = minigames.build_auction_start_message("Espada de Ouro")

        self.assertIn("NOVO LEILÃO INICIADO", message)
        self.assertIn("Espada de Ouro", message)

    def test_build_auction_approval_embed_mentions_timeout(self):
        embed = minigames.build_auction_approval_embed("Espada de Ouro", "legendary", 150)

        self.assertEqual(embed.title, "📝 Solicitação de Leilão")
        self.assertIn("5 minutos", embed.description)
        self.assertIn("Espada de Ouro", embed.description)

    def test_build_auction_embed_shows_current_participant_and_last_bid(self):
        embed = minigames.build_auction_embed("Espada de Ouro", "legendary", 150, minigames.datetime.now() + minigames.timedelta(hours=1))

        self.assertEqual(embed.fields[0].name, "⏳ Tempo restante")
        self.assertEqual(embed.fields[1].name, "👤 Participante atual")
        self.assertEqual(embed.fields[1].value, "Nenhum")
        self.assertEqual(embed.fields[2].name, "💸 Último lance")
        self.assertEqual(embed.fields[2].value, "Nenhum")

    def test_format_time_remaining_formats_hours_minutes_and_seconds(self):
        self.assertEqual(minigames.format_time_remaining(3661), "1h 1m 1s")


class BatalharConsumeFishTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /eco batalhar checava presença de peixe no inventário mas
    nunca consumia nada — o "custo" nunca era exercido (auditoria Fase 4).
    """

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, display_name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
            original_response=AsyncMock(),
        )

    async def test_batalhar_consumes_one_qualifying_fish(self):
        conn = _make_conn()
        uid, opp_id = 10, 11
        ensure_user(conn, uid, "Tester")
        add_inventory_item(conn, uid, "Sardinha", 2)

        interaction = self._make_interaction(uid)
        opponent = SimpleNamespace(id=opp_id, bot=False)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await minigames.batalhar.callback(interaction, opponent)

        self.assertEqual(get_inventory(conn, uid).get("Sardinha"), 1)
        interaction.response.send_message.assert_called_once()

    async def test_batalhar_refuses_without_qualifying_fish(self):
        conn = _make_conn()
        uid, opp_id = 12, 13
        ensure_user(conn, uid, "Tester")
        add_inventory_item(conn, uid, "Bota Velha", 5)  # lixo, não conta como peixe de batalha

        interaction = self._make_interaction(uid)
        opponent = SimpleNamespace(id=opp_id, bot=False)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await minigames.batalhar.callback(interaction, opponent)

        self.assertIn("precisa de peixes", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_inventory(conn, uid).get("Bota Velha"), 5)


class BattleViewAntiReplayTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: BattleView não tinha guarda `finished` — clique duplo no
    ataque vencedor podia pagar a recompensa mais de uma vez.
    """

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, display_name=name),
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )

    async def test_double_click_on_winning_attack_pays_only_once(self):
        conn = _make_conn()
        uid = 20
        ensure_user(conn, uid, "Tester")

        view = minigames.BattleView(uid, 21, user_hp=100, opp_hp=1)
        interaction = self._make_interaction(uid)

        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(minigames.random, "randint", return_value=5), \
             patch.object(minigames.random, "choice", return_value="Fogo"):
            await view._attack(interaction, "Água")
            self.assertTrue(view.finished)
            wallet_after_win = get_wallet(conn, uid)

            # Clique duplo: a batalha já terminou, não pode pagar de novo.
            second_interaction = self._make_interaction(uid)
            await view._attack(second_interaction, "Água")

        self.assertEqual(get_wallet(conn, uid), wallet_after_win)
        second_interaction.response.send_message.assert_called_once()
        self.assertIn("já encerrada", second_interaction.response.send_message.call_args.args[0])


class MemoriaCooldownTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /eco memoria pagava sem custo real nem cooldown (20-48k
    Sachês/hora de graça, auditoria Fase 4) — agora tem cooldown real, hoje
    de 1800s (Fase 3). Os valores vivem em MEMORIA_PREMIO/
    MEMORIA_COOLDOWN_SECONDS e são balanceamento (ver MemoriaPremioTests),
    não regra deste teste: aqui só se testa que o cooldown é respeitado.
    """

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, display_name=name),
            response=SimpleNamespace(send_message=AsyncMock()),
            original_response=AsyncMock(),
        )

    async def test_blocked_within_cooldown_window(self):
        conn = _make_conn()
        uid = 30
        ensure_user(conn, uid, "Tester")
        set_cooldown(conn, uid, "last_memoria", datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"))

        interaction = self._make_interaction(uid)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await minigames.memoria.callback(interaction)

        self.assertIn("embaralhadas", interaction.response.send_message.call_args.args[0])

    async def test_allowed_after_cooldown_expires_and_reserves_it_again(self):
        conn = _make_conn()
        uid = 31
        ensure_user(conn, uid, "Tester")
        expired = datetime.now() - timedelta(seconds=minigames.MEMORIA_COOLDOWN_SECONDS + 5)
        set_cooldown(conn, uid, "last_memoria", expired.strftime("%Y-%m-%d %H:%M:%S.%f"))

        interaction = self._make_interaction(uid)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await minigames.memoria.callback(interaction)

        interaction.response.send_message.assert_called_once()
        # Reservou um novo cooldown (não é mais o "expired" antigo).
        new_last = get_cooldowns(conn, uid)["last_memoria"]
        self.assertNotEqual(new_last, expired.strftime("%Y-%m-%d %H:%M:%S.%f"))

    async def test_first_time_player_is_allowed(self):
        conn = _make_conn()
        uid = 32
        ensure_user(conn, uid, "Tester")

        interaction = self._make_interaction(uid)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await minigames.memoria.callback(interaction)

        interaction.response.send_message.assert_called_once()
        self.assertIsNotNone(get_cooldowns(conn, uid)["last_memoria"])


class MemoriaViewAntiReplayTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: reforça a proteção contra pagamento duplo na última
    dupla encontrada (guarda `finished` explícita, mesmo padrão de
    BlackjackView/CrashView) além da proteção implícita via `revealed`.
    """

    def _make_interaction(self, user_id, name="Tester"):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, display_name=name),
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )

    async def test_finished_flag_blocks_further_clicks_after_win(self):
        conn = _make_conn()
        uid = 40
        ensure_user(conn, uid, "Tester")

        # Tabuleiro com as 6 duplas já casadas, faltando só clicar a última.
        cartas = ["🐟", "🐟", "🐠", "🐠", "🐡", "🐡", "🦑", "🦑", "🐙", "🐙", "🦀", "🦀"]
        view = minigames.MemoriaView(uid, cartas)
        for i in range(10):
            view.revealed[i] = True
        view.pairs_found = 5
        view.first_pick = 10  # 🦀 já virada, esperando o par (idx 11)

        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            winning_interaction = self._make_interaction(uid)
            await view.children[11].callback(winning_interaction)

        self.assertTrue(view.finished)
        wallet_after_win = get_wallet(conn, uid)
        # Lê a constante em vez de um literal: este teste é sobre pagamento
        # duplo, não sobre o valor do prêmio — travar o número aqui faz um
        # ajuste de balanceamento quebrar um teste de anti-replay.
        self.assertEqual(wallet_after_win, minigames.MEMORIA_PREMIO)

        # Qualquer clique adicional (mesmo em carta nunca virada) é
        # bloqueado pela guarda `finished`, não paga de novo.
        another_interaction = self._make_interaction(uid)
        with patch.object(minigames, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.children[0].callback(another_interaction)

        self.assertEqual(get_wallet(conn, uid), wallet_after_win)
        self.assertIn("já encerrado", another_interaction.response.send_message.call_args.args[0])


class MemoriaPremioTests(unittest.TestCase):
    """Ajuste de balanceamento: o Aquário rendia mais que a pescaria da faixa
    de entrada, sem custo e sem depender de equipamento.

    Fase 3 reajustou o PAR (prêmio, cooldown), não só o prêmio: 140 a cada
    30 minutos = 280/h, contra 244,7/h da Vara de Bambu. O prêmio por rodada
    subiu e a renda por hora caiu 4,3x — é a frequência que estava errada.
    """

    def _pesca_inicial_por_hora(self):
        """Sachês/hora da Vara de Bambu, o piso da progressão de verdade."""
        from cogs.economia import ROD_STATS, FISH_DB, TRASH_ITEMS, TRASH_ROLL_RATIO

        r = ROD_STATS["vara_bambu"]
        climas = {"normal": (1.0, 1.0, 0, 0.7), "bad": (0.5, 2.0, 0, 0.2), "good": (1.5, 0.5, 1, 0.1)}
        iniciais = [p for p in FISH_DB if p[4] == 0 and p[0] not in TRASH_ITEMS]
        media = lambda p: (p[1] + p[2]) / 2
        ev = 0.0
        for _, (lm, tm, tb, peso) in climas.items():
            pt = max(0.0, min(100.0, r["trash"] * tm)) / 100.0
            mult = r["luck"] * lm
            parcial = pt * (1 - TRASH_ROLL_RATIO) * (sum(map(media, iniciais)) / len(iniciais)) * mult
            pool = [p for p in FISH_DB if 0 < p[4] <= r["tier"] + tb] or iniciais
            parcial += (1 - pt) * (
                sum(media(p) for p in pool if p[0] not in TRASH_ITEMS) / len(pool)
            ) * mult
            ev += peso * parcial
        return ev * 3600 / int(300 * r["cd"])

    def test_premio_and_cooldown_pair(self):
        """O par inteiro é o balanceamento — o prêmio sozinho não diz nada."""
        self.assertEqual(minigames.MEMORIA_PREMIO, 140)
        self.assertEqual(minigames.MEMORIA_COOLDOWN_SECONDS, 1800)

    def test_hourly_rate(self):
        por_hora = minigames.MEMORIA_PREMIO * 3600 / minigames.MEMORIA_COOLDOWN_SECONDS
        self.assertEqual(por_hora, 280)

    def test_memoria_sits_just_above_the_starter_rod_band(self):
        """O invariante de desenho da Fase 3: o Aquário rende um pouco ACIMA
        da pescaria de entrada — o suficiente para ser uma escolha de renda
        passiva, longe o bastante de ser um substituto da pescaria ativa.

        A faixa é 10-20% acima. Abaixo disso ninguém joga; muito acima e a
        vara vira decoração, que era o problema que o valor antigo criava
        (1.200/h contra 244,7/h, quase 5x).
        """
        memoria_h = minigames.MEMORIA_PREMIO * 3600 / minigames.MEMORIA_COOLDOWN_SECONDS
        pesca_h = self._pesca_inicial_por_hora()
        razao = memoria_h / pesca_h
        self.assertGreater(razao, 1.10, f"Aquário caiu para {memoria_h}/h contra {pesca_h:.1f}/h da pesca")
        self.assertLess(razao, 1.20, f"Aquário subiu para {memoria_h}/h contra {pesca_h:.1f}/h da pesca")

    def test_cooldown_is_a_deliberate_wait_not_a_second_fishing_rod(self):
        """A espera tem que ser de outra ordem de grandeza que a da vara,
        senão o Aquário volta a ser uma segunda pescaria rodando em paralelo.
        """
        from cogs.economia import ROD_STATS

        cd_vara_inicial = 300 * ROD_STATS["vara_bambu"]["cd"]
        self.assertGreaterEqual(minigames.MEMORIA_COOLDOWN_SECONDS, 6 * cd_vara_inicial)


if __name__ == "__main__":
    unittest.main()
