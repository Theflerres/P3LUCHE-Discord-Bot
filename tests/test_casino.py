import asyncio
import re
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import casino
from economy_db import ensure_user, ensure_v4_tables, get_wallet, modify_wallet


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_v4_tables(conn)
    return conn


def _make_interaction(user_id, name="Tester"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=name),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(),
    )


class CasinoTests(unittest.IsolatedAsyncioTestCase):
    async def test_blackjack_push_refunds_bet(self):
        view = casino.BlackjackView(123, 10, ["T♠", "7♥", "T♦", "7♣", "A♠", "A♥"])
        view.player = ["T♠", "7♥"]
        view.dealer = ["T♦", "7♣"]

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn="db")), patch.object(casino, "modify_wallet") as mock_modify:
            await view._finish(None)

        mock_modify.assert_called_once_with("db", 123, 10)


class BlackjackDoubleAndLossTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: 'Dobrar' pagava lucro líquido zero (bug de EV descrito na
    Fase 4) e as 3 ramificações de derrota (timeout/estouro/banca vence)
    debitavam a aposta de novo, apesar dela já ter sido debitada por inteiro
    na abertura/dobra (achado adicional durante esta correção — mesma
    categoria de bug, cobrando 2x o valor apostado em toda derrota).
    """

    def _make_view(self, uid, aposta, doubled=False):
        view = casino.BlackjackView(uid, aposta, ["2♠", "3♠", "4♠", "5♠", "6♠", "7♠", "8♠", "9♠"])
        view.doubled = doubled
        return view

    async def test_doubled_win_pays_profit_equal_to_doubled_stake(self):
        conn = _make_conn()
        uid = 100
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -20, "Tester")  # simula os 20 já debitados (10 base + 10 da dobra)

        view = self._make_view(uid, aposta=20, doubled=True)
        view.player = ["T♠", "9♥"]  # 19
        view.dealer = ["T♦", "6♣"]  # 16 -> hits para <17 ; vamos forçar não estourar
        view.deck = ["2♣"]  # dealer puxa até >=17: 16+2=18, ainda perde pra 19

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view._finish(None)

        # Antes do fix: pagava só 20 (lucro líquido zero). Correto: paga 40
        # (aposta dobrada de volta + lucro 1:1 sobre o valor já dobrado).
        self.assertEqual(get_wallet(conn, uid), 1000 - 20 + 40)

    async def test_non_doubled_win_still_pays_double_the_stake(self):
        conn = _make_conn()
        uid = 101
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -10, "Tester")

        view = self._make_view(uid, aposta=10, doubled=False)
        view.player = ["T♠", "9♥"]
        view.dealer = ["T♦", "6♣"]
        view.deck = ["2♣"]

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view._finish(None)

        self.assertEqual(get_wallet(conn, uid), 1000 - 10 + 20)

    async def test_bust_loss_does_not_charge_twice(self):
        conn = _make_conn()
        uid = 102
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -10, "Tester")

        view = self._make_view(uid, aposta=10)
        view.player = ["T♠", "9♥", "5♦"]  # 24, estourou

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view._finish(None)

        # A aposta já saiu na abertura; estourar não pode debitar de novo.
        self.assertEqual(get_wallet(conn, uid), 1000 - 10)

    async def test_dealer_wins_does_not_charge_twice(self):
        conn = _make_conn()
        uid = 103
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -10, "Tester")

        view = self._make_view(uid, aposta=10)
        view.player = ["9♠", "9♥"]  # 18
        view.dealer = ["T♦", "9♣"]  # 19
        view.deck = []

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view._finish(None)

        self.assertEqual(get_wallet(conn, uid), 1000 - 10)

    async def test_timeout_loss_does_not_charge_twice(self):
        conn = _make_conn()
        uid = 104
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -10, "Tester")

        view = self._make_view(uid, aposta=10)
        view.deck = []

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view._finish(None, timeout=True)

        self.assertEqual(get_wallet(conn, uid), 1000 - 10)


class PokerEscrowTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /casino poker só debitava a aposta no showdown, deixando o
    saldo "livre" durante toda a mão (até 60s) mesmo com a aposta
    comprometida — mesmo padrão de fix já usado no Blackjack.
    """

    async def test_poker_debits_bet_upfront_when_table_opens(self):
        conn = _make_conn()
        uid = 110
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await casino.poker.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 900)

    async def test_win_nets_profit_equal_to_stake(self):
        conn = _make_conn()
        uid = 111
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -100, "Tester")  # simula o débito já feito na abertura

        view = casino.PokerView(uid, 100, casino._new_deck())
        interaction = _make_interaction(uid)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_best_of_seven", side_effect=[(5, [12]), (0, [1])]):
            view.community = ["2♠", "3♠", "4♠", "5♥", "6♥"]
            await view.showdown.callback(interaction)

        self.assertEqual(get_wallet(conn, uid), 1000 - 100 + 200)

    async def test_loss_does_not_charge_again(self):
        conn = _make_conn()
        uid = 112
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -100, "Tester")

        view = casino.PokerView(uid, 100, casino._new_deck())
        interaction = _make_interaction(uid)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_best_of_seven", side_effect=[(0, [1]), (5, [12])]):
            view.community = ["2♠", "3♠", "4♠", "5♥", "6♥"]
            await view.showdown.callback(interaction)

        self.assertEqual(get_wallet(conn, uid), 1000 - 100)

    async def test_tie_refunds_stake_to_net_zero(self):
        conn = _make_conn()
        uid = 113
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -100, "Tester")

        view = casino.PokerView(uid, 100, casino._new_deck())
        interaction = _make_interaction(uid)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_best_of_seven", side_effect=[(3, [7]), (3, [7])]):
            view.community = ["2♠", "3♠", "4♠", "5♥", "6♥"]
            await view.showdown.callback(interaction)

        self.assertEqual(get_wallet(conn, uid), 1000)

    async def test_timeout_resolves_as_loss_without_extra_charge(self):
        conn = _make_conn()
        uid = 114
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -100, "Tester")

        view = casino.PokerView(uid, 100, casino._new_deck())
        view.message = SimpleNamespace(edit=AsyncMock())

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.on_timeout()

        self.assertTrue(view.finished)
        self.assertEqual(get_wallet(conn, uid), 1000 - 100)
        view.message.edit.assert_called_once()

    async def test_double_click_on_showdown_does_not_pay_twice(self):
        conn = _make_conn()
        uid = 115
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")
        modify_wallet(conn, uid, -100, "Tester")

        view = casino.PokerView(uid, 100, casino._new_deck())
        view.community = ["2♠", "3♠", "4♠", "5♥", "6♥"]

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_best_of_seven", return_value=(5, [12])):
            first = _make_interaction(uid)
            await view.showdown.callback(first)
            wallet_after_win = get_wallet(conn, uid)

            second = _make_interaction(uid)
            await view.showdown.callback(second)

        self.assertEqual(get_wallet(conn, uid), wallet_after_win)
        second.response.send_message.assert_called_once()
        self.assertIn("já encerrada", second.response.send_message.call_args.args[0])


class CrashOvershootTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: o multiplicador podia ultrapassar o crash_point antes de
    parar — o valor pós-crash ficava exposto e o Cash Out continuava ativo
    por ~0.5s depois do crash real.
    """

    async def test_displayed_multiplier_never_reaches_or_exceeds_crash_point(self):
        conn = _make_conn()
        uid = 120
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        msg = SimpleNamespace(edit=AsyncMock())
        interaction.followup.send = AsyncMock(return_value=msg)

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_draw_crash_point", return_value=2.0), \
             patch.object(casino.random, "uniform", return_value=0.5), \
             patch.object(casino, "_crash_wait", new=AsyncMock()):
            await casino.crash.callback(interaction, 100)

        shown_multipliers = []
        for call in msg.edit.call_args_list:
            content = call.kwargs.get("content", "")
            match = re.search(r"\*\*([\d.]+)x\*\*", content)
            if match and "Multiplicador" in content:
                shown_multipliers.append(float(match.group(1)))

        self.assertTrue(shown_multipliers, "esperava pelo menos uma atualização de multiplicador")
        for value in shown_multipliers:
            self.assertLess(value, 2.0)

        final_call = msg.edit.call_args_list[-1]
        self.assertIn("CRASH", final_call.kwargs.get("content", ""))
        final_view = final_call.kwargs.get("view")
        self.assertTrue(all(child.disabled for child in final_view.children))

    async def test_cashout_still_possible_before_crash_and_pays_shown_multiplier(self):
        view = casino.CrashView(1, 100, crash_point=5.0)
        view.multiplier = 1.8
        conn = _make_conn()
        ensure_user(conn, 1, "Tester")

        interaction = _make_interaction(1)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.cashout.callback(interaction)

        self.assertTrue(view.cashed_out)
        self.assertEqual(get_wallet(conn, 1), int(100 * 1.8))

    async def test_cashout_depois_do_crash_e_recusado(self):
        """Regressão: entre o crash e a edição final da mensagem (janela que
        sob rate limit durava segundos) o botão continuava vivo e pagava o
        multiplicador pré-crash. `ended` fecha essa janela.
        """
        view = casino.CrashView(1, 100, crash_point=2.0)
        view.multiplier = 1.9
        view.ended.set()
        conn = _make_conn()
        ensure_user(conn, 1, "Tester")

        interaction = _make_interaction(1)
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
            await view.cashout.callback(interaction)

        self.assertFalse(view.cashed_out)
        self.assertEqual(get_wallet(conn, 1), 0)
        interaction.response.edit_message.assert_not_called()
        self.assertIn("crashou", interaction.response.send_message.call_args.args[0])

    async def test_saque_interrompe_o_loop_antes_da_proxima_edicao(self):
        """O saque marca `ended` de forma síncrona, então o loop nem chega a
        emitir a edição seguinte, que sobrescreveria a confirmação do saque.
        """
        conn = _make_conn()
        uid = 121
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        msg = SimpleNamespace(edit=AsyncMock())
        interaction.followup.send = AsyncMock(return_value=msg)

        async def saca_no_primeiro_tick(view, _seconds):
            view.cashed_out = True
            view.ended.set()

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino, "_draw_crash_point", return_value=50.0), \
             patch.object(casino.random, "uniform", return_value=0.5), \
             patch.object(casino, "_crash_wait", new=saca_no_primeiro_tick):
            await casino.crash.callback(interaction, 100)

        self.assertEqual(msg.edit.call_count, 1)
        self.assertNotIn("CRASH", msg.edit.call_args.kwargs.get("content", ""))
        self.assertNotIn(uid, casino._crash_rounds_ativos)

    async def test_rodada_simultanea_do_mesmo_jogador_e_recusada(self):
        """Rodadas paralelas do mesmo usuário multiplicavam as requisições no
        mesmo bucket de canal — origem do travamento em DM.
        """
        conn = _make_conn()
        uid = 122
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        casino._crash_rounds_ativos.add(uid)
        try:
            with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)):
                await casino.crash.callback(interaction, 100)
        finally:
            casino._crash_rounds_ativos.discard(uid)

        self.assertIn("em andamento", interaction.response.send_message.call_args.args[0])
        self.assertEqual(get_wallet(conn, uid), 1000)

    def test_cadencia_de_edicao_respeita_o_rate_limit_do_discord(self):
        """~5 edições / 5s por canal. O tick antigo (0.5s) era 4x acima."""
        self.assertGreaterEqual(casino.CRASH_TICK_SECONDS, 1.0)
        # Velocidade de subida por segundo preservada (era 0.2-1.0/s).
        self.assertAlmostEqual(casino.CRASH_STEP_MIN / casino.CRASH_TICK_SECONDS, 0.2)
        self.assertAlmostEqual(casino.CRASH_STEP_MAX / casino.CRASH_TICK_SECONDS, 1.0)


class CrashHouseEdgeFormulaTests(unittest.TestCase):
    """Decisão de design confirmada: house edge ~9% (faixa 8-10% "alto
    risco/recompensa"), via P(crash_point >= m) = (1 - house_edge) / m —
    dá EV = -house_edge * aposta constante, não importa o multiplicador de
    saque escolhido.
    """

    def test_matches_closed_form_for_a_given_draw(self):
        with patch.object(casino.random, "uniform", return_value=0.5):
            cp = casino._draw_crash_point(house_edge=0.09)

        self.assertAlmostEqual(cp, (1 - 0.09) / 0.5)

    def test_never_below_one(self):
        # r=0.99 -> (1-0.09)/0.99 = 0.919, abaixo de 1.0 -> precisa ser
        # arredondado pra cima (o multiplicador começa em 1.0x).
        with patch.object(casino.random, "uniform", return_value=0.99):
            cp = casino._draw_crash_point(house_edge=0.09)

        self.assertEqual(cp, 1.0)

    def test_capped_at_max_multiplier_for_extreme_draws(self):
        with patch.object(casino.random, "uniform", return_value=0.001):
            cp = casino._draw_crash_point(house_edge=0.09)

        self.assertEqual(cp, casino.CRASH_MAX_MULTIPLIER)


class SlotsPaytableTests(unittest.IsolatedAsyncioTestCase):
    """Decisão de design confirmada: RTP alvo ~80% (alto risco) — antes
    RTP≈40,3%. Mantidos os 6 símbolos equiprováveis; só a tabela de
    multiplicadores mudou: 2-de-3 continua 1x, trinca de fruta 10x (era 3x),
    trinca 🔔 25x (era 5x), trinca 💎 48x (era 10x).
    """

    async def test_diamond_triple_pays_48x(self):
        conn = _make_conn()
        uid = 140
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        choices = iter((["🍒", "🍋", "🍊"] * 3) + ["💎", "💎", "💎"])
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.random, "choice", side_effect=lambda seq: next(choices)), \
             patch.object(casino.asyncio, "sleep", new=AsyncMock()):
            await casino.slots.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 1000 - 100 + 100 * 48)

    async def test_bell_triple_pays_25x(self):
        conn = _make_conn()
        uid = 141
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        choices = iter((["🍒", "🍋", "🍊"] * 3) + ["🔔", "🔔", "🔔"])
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.random, "choice", side_effect=lambda seq: next(choices)), \
             patch.object(casino.asyncio, "sleep", new=AsyncMock()):
            await casino.slots.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 1000 - 100 + 100 * 25)

    async def test_fruit_triple_pays_10x(self):
        conn = _make_conn()
        uid = 142
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        choices = iter((["🍒", "🍋", "🍊"] * 3) + ["🍒", "🍒", "🍒"])
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.random, "choice", side_effect=lambda seq: next(choices)), \
             patch.object(casino.asyncio, "sleep", new=AsyncMock()):
            await casino.slots.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 1000 - 100 + 100 * 10)

    async def test_two_of_three_still_just_refunds_the_bet(self):
        conn = _make_conn()
        uid = 143
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        choices = iter((["🍒", "🍋", "🍊"] * 3) + ["🍒", "🍒", "🍋"])
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.random, "choice", side_effect=lambda seq: next(choices)), \
             patch.object(casino.asyncio, "sleep", new=AsyncMock()):
            await casino.slots.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 1000)  # débito de 100 + prêmio de 100 = líquido zero

    async def test_no_match_loses_the_bet(self):
        conn = _make_conn()
        uid = 144
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 1000, "Tester")

        interaction = _make_interaction(uid)
        choices = iter((["🍒", "🍋", "🍊"] * 3) + ["🍒", "🍋", "🍊"])
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.random, "choice", side_effect=lambda seq: next(choices)), \
             patch.object(casino.asyncio, "sleep", new=AsyncMock()):
            await casino.slots.callback(interaction, 100)

        self.assertEqual(get_wallet(conn, uid), 900)


class SlotsEscrowTests(unittest.IsolatedAsyncioTestCase):
    """Regressão: /casino slots só debitava a aposta DEPOIS da animação de
    giro (~3s de sleeps), deixando uma janela em que múltiplas chamadas
    passavam pela checagem de saldo antes de qualquer uma debitar.
    """

    async def test_debit_happens_before_spin_animation_starts(self):
        conn = _make_conn()
        uid = 130
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 500, "Tester")

        interaction = _make_interaction(uid)
        real_sleep = asyncio.sleep

        async def instant_yield(*_args, **_kwargs):
            await real_sleep(0)

        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.asyncio, "sleep", side_effect=instant_yield):
            task = asyncio.create_task(casino.slots.callback(interaction, 100))
            await asyncio.sleep(0)
            # No ponto em que a corrotina cede controle pela 1ª vez de
            # verdade (dentro do loop de animação), o débito já aconteceu.
            self.assertEqual(get_wallet(conn, uid), 400)
            await task

    async def test_concurrent_spins_cannot_double_spend_during_animation(self):
        conn = _make_conn()
        uid = 131
        ensure_user(conn, uid, "Tester")
        modify_wallet(conn, uid, 150, "Tester")  # só dá pra bancar 1 aposta de 100, não 2

        interaction1 = _make_interaction(uid)
        interaction2 = _make_interaction(uid)
        real_sleep = asyncio.sleep

        async def instant_yield(*_args, **_kwargs):
            await real_sleep(0)

        # Símbolos distintos travados: o resultado do giro em si é
        # irrelevante pra esse teste (é sobre a corrida do escrow, não sobre
        # o paytable) — sem isso, a 1ª chamada podia bater prêmio por acaso
        # e o valor final ficaria imprevisível.
        with patch.object(casino, "get_bot_instance", return_value=SimpleNamespace(db_conn=conn)), \
             patch.object(casino.asyncio, "sleep", side_effect=instant_yield), \
             patch.object(casino.random, "choice", return_value="🍒"):
            task1 = asyncio.create_task(casino.slots.callback(interaction1, 100))
            await asyncio.sleep(0)
            self.assertFalse(task1.done())

            # 2ª chamada enquanto a 1ª ainda está "girando" — tem que ser
            # recusada por saldo insuficiente, não duplicar o gasto.
            await casino.slots.callback(interaction2, 100)

            await task1

        self.assertIn("Saldo insuficiente", interaction2.response.send_message.call_args.args[0])
        # A 1ª chamada rolou 🍒🍒🍒 (trinca de fruta = 10x): 150 - 100 (débito) + 1000 (prêmio).
        self.assertEqual(get_wallet(conn, uid), 150 - 100 + 100 * 10)


if __name__ == "__main__":
    unittest.main()
