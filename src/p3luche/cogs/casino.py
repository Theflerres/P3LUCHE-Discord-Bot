"""
Casino P3LUCHE — poker, blackjack, crash e slots.

ATIVO: reativado em main.py após as correções da Fase 4 (escrow, bug do
Dobrar, overshoot do crash, house edge/RTP de crash e slots), todas
cobertas por tests/test_casino.py.
"""
from __future__ import annotations

import asyncio
import math
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import get_bot_instance
from economy_db import get_wallet, modify_wallet
from utils import log_to_gui

casino_group = app_commands.Group(name="casino", description="Jogos de casino do P3LUCHE.")


def _check_bet(interaction: discord.Interaction, aposta: int) -> tuple[bool, str]:
    if aposta < 10:
        return False, "❌ Aposta mínima: 10 Sachês."
    wallet = get_wallet(get_bot_instance().db_conn, interaction.user.id)
    if wallet < aposta:
        return False, f"💸 Saldo insuficiente ({wallet} < {aposta})."
    return True, ""


# --- POKER SIMPLIFICADO ---

RANKS = "23456789TJQKA"
SUITS = "♠♥♦♣"


def _new_deck() -> list[str]:
    return [f"{r}{s}" for s in SUITS for r in RANKS]


def _hand_score(cards: list[str]) -> tuple[int, list[int]]:
    """Pontuação simples: tipo de mão + desempate por ranks."""
    ranks = sorted([RANKS.index(c[0]) for c in cards], reverse=True)
    suits = [c[1] for c in cards]
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    freq = sorted(counts.values(), reverse=True)
    flush = len(set(suits)) == 1
    straight = (max(ranks) - min(ranks) == 4 and len(set(ranks)) == 5) or ranks == [
        12,
        3,
        2,
        1,
        0,
    ]
    if straight and flush:
        return (8, ranks)
    if freq[0] == 4:
        quad = [r for r, c in counts.items() if c == 4][0]
        kicker = [r for r, c in counts.items() if c == 1][0]
        return (7, [quad, kicker])
    if freq[0] == 3 and freq[1] == 2:
        trip = [r for r, c in counts.items() if c == 3][0]
        pair = [r for r, c in counts.items() if c == 2][0]
        return (6, [trip, pair])
    if flush:
        return (5, ranks)
    if straight:
        return (4, ranks)
    if freq[0] == 3:
        trip = [r for r, c in counts.items() if c == 3][0]
        kickers = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (3, [trip] + kickers)
    if freq[0] == 2 and freq[1] == 2:
        pairs = sorted([r for r, c in counts.items() if c == 2], reverse=True)
        kicker = [r for r, c in counts.items() if c == 1][0]
        return (2, pairs + [kicker])
    if freq[0] == 2:
        pair = [r for r, c in counts.items() if c == 2][0]
        kickers = sorted([r for r, c in counts.items() if c == 1], reverse=True)
        return (1, [pair] + kickers)
    return (0, ranks)


def _best_of_seven(cards: list[str]) -> tuple[int, list[int]]:
    from itertools import combinations

    best = (-1, [])
    for combo in combinations(cards, 5):
        score = _hand_score(list(combo))
        if score > best:
            best = score
    return best


HAND_NAMES = {
    8: "Straight Flush",
    7: "Quadra",
    6: "Full House",
    5: "Flush",
    4: "Sequência",
    3: "Trinca",
    2: "Dois Pares",
    1: "Par",
    0: "Carta Alta",
}


class PokerView(discord.ui.View):
    def __init__(self, user_id: int, aposta: int, deck: list[str]):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.aposta = aposta
        self.deck = deck
        self.player = [deck.pop(), deck.pop()]
        self.bot_hand = [deck.pop(), deck.pop()]
        self.community: list[str] = []
        self.finished = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é sua mesa.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        # A aposta já foi debitada na abertura da mesa (escrow) — sem
        # showdown, ela fica perdida (mesma semântica de "derrota
        # automática" já usada em BlackjackView.on_timeout).
        if self.message:
            try:
                await self.message.edit(
                    content=f"⏰ Tempo esgotado — mesa fechada, aposta perdida ({self.aposta} Sachês).",
                    view=self,
                )
            except discord.HTTPException:
                pass

    def _embed(self, reveal_bot: bool = False) -> discord.Embed:
        embed = discord.Embed(title="🃏 Texas Hold'em", color=discord.Color.dark_green())
        embed.add_field(name="Suas cartas", value=" ".join(self.player), inline=False)
        bot_cards = " ".join(self.bot_hand) if reveal_bot else "🂠 🂠"
        embed.add_field(name="Bot", value=bot_cards, inline=False)
        board = " ".join(self.community) if self.community else "—"
        embed.add_field(name="Mesa", value=board, inline=False)
        embed.set_footer(text=f"Aposta: {self.aposta} Sachês")
        return embed

    @discord.ui.button(label="Revelar Flop", style=discord.ButtonStyle.primary)
    async def flop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.community) > 0:
            return await interaction.response.send_message("❌ Já revelado.", ephemeral=True)
        self.community.extend([self.deck.pop() for _ in range(3)])
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Turn + River", style=discord.ButtonStyle.secondary)
    async def turn_river(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.community) < 3:
            return await interaction.response.send_message("❌ Revele o flop primeiro.", ephemeral=True)
        if len(self.community) >= 5:
            return await interaction.response.send_message("❌ Mesa completa.", ephemeral=True)
        while len(self.community) < 5:
            self.community.append(self.deck.pop())
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Showdown", style=discord.ButtonStyle.success, emoji="💰")
    async def showdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.community) < 5:
            return await interaction.response.send_message("❌ Complete a mesa primeiro.", ephemeral=True)
        if self.finished:
            return await interaction.response.send_message("❌ Mesa já encerrada.", ephemeral=True)
        self.finished = True
        for child in self.children:
            child.disabled = True

        p_score = _best_of_seven(self.player + self.community)
        b_score = _best_of_seven(self.bot_hand + self.community)
        conn = get_bot_instance().db_conn
        uid = interaction.user.id
        name = interaction.user.display_name

        # A aposta já foi debitada na abertura da mesa (escrow, mesmo padrão
        # do Blackjack) — vitória paga a aposta de volta + o lucro (1:1);
        # derrota não debita de novo (já foi debitada); empate devolve.
        if p_score > b_score:
            modify_wallet(conn, uid, self.aposta * 2, name)
            result = f"🏆 Você venceu! +{self.aposta} Sachês\nMão: **{HAND_NAMES[p_score[0]]}**"
        elif p_score < b_score:
            result = f"💀 O bot venceu. -{self.aposta} Sachês\nMão do bot: **{HAND_NAMES[b_score[0]]}**"
        else:
            modify_wallet(conn, uid, self.aposta, name)
            result = "🤝 Empate! Aposta devolvida."

        embed = self._embed(reveal_bot=True)
        embed.description = result
        await interaction.response.edit_message(embed=embed, view=self)


@casino_group.command(name="poker", description="Texas Hold'em simplificado contra o bot.")
@app_commands.describe(aposta="Valor em Sachês")
async def poker(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    # Escrow: debita a aposta ANTES de abrir a mesa (mesmo padrão do
    # Blackjack), não só no showdown — fecha a janela em que o saldo fica
    # "livre" durante toda a mão (até 60s) mesmo com a aposta comprometida.
    modify_wallet(get_bot_instance().db_conn, interaction.user.id, -aposta, interaction.user.display_name)
    deck = _new_deck()
    random.shuffle(deck)
    view = PokerView(interaction.user.id, aposta, deck)
    await interaction.response.send_message(embed=view._embed(), view=view)
    view.message = await interaction.original_response()


# --- BLACKJACK ---

def _bj_value(hand: list[str]) -> int:
    total = 0
    aces = 0
    for c in hand:
        r = c[0]
        if r in "TJQK":
            total += 10
        elif r == "A":
            aces += 1
            total += 11
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


class BlackjackView(discord.ui.View):
    def __init__(self, user_id: int, aposta: int, deck: list[str]):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.aposta = aposta
        self.deck = deck
        self.player = [deck.pop(), deck.pop()]
        self.dealer = [deck.pop(), deck.pop()]
        self.doubled = False
        self.finished = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é sua vez.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if not self.finished:
            await self._finish(None, timeout=True)

    def _embed(self, hide_dealer: bool = True) -> discord.Embed:
        embed = discord.Embed(title="🂡 Blackjack 21", color=discord.Color.dark_red())
        p_val = _bj_value(self.player)
        embed.add_field(
            name=f"Você ({p_val})",
            value=" ".join(self.player),
            inline=False,
        )
        if hide_dealer and not self.finished:
            d_show = f"{self.dealer[0]} 🂠"
            embed.add_field(name="Banca (?)", value=d_show, inline=False)
        else:
            d_val = _bj_value(self.dealer)
            embed.add_field(
                name=f"Banca ({d_val})",
                value=" ".join(self.dealer),
                inline=False,
            )
        embed.set_footer(text=f"Aposta: {self.aposta} Sachês")
        return embed

    async def _finish(self, interaction: discord.Interaction | None, timeout: bool = False):
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True

        while _bj_value(self.dealer) < 17 and self.deck:
            self.dealer.append(self.deck.pop())

        p_val = _bj_value(self.player)
        d_val = _bj_value(self.dealer)
        conn = get_bot_instance().db_conn
        uid = self.user_id

        # A aposta (self.aposta, já dobrada se doubled=True) foi debitada
        # integralmente no ato da aposta/dobra — nenhuma derrota pode debitar
        # de novo aqui (bug encontrado nesta correção: as 3 ramificações de
        # derrota debitavam -self.aposta outra vez, cobrando 2x o valor
        # apostado em toda derrota). Vitória paga a aposta de volta + o
        # lucro (1:1) sobre o valor JÁ dobrado — não existe mais caso
        # especial pra `doubled`, já que self.aposta reflete o valor atual.
        if timeout:
            msg = "⏰ Tempo esgotado — derrota automática."
        elif p_val > 21:
            msg = f"💥 Estourou ({p_val})! -{self.aposta} Sachês"
        elif d_val > 21 or p_val > d_val:
            win = self.aposta * 2
            modify_wallet(conn, uid, win)
            msg = f"🏆 Você venceu! +{win} Sachês"
        elif p_val == d_val:
            modify_wallet(conn, uid, self.aposta)
            msg = f"🤝 Empate! Aposta devolvida (+{self.aposta} Sachês)"
        else:
            msg = f"💀 Banca vence ({d_val}). -{self.aposta} Sachês"

        embed = self._embed(hide_dealer=False)
        embed.description = msg
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message:
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Pedir carta", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.append(self.deck.pop())
        if _bj_value(self.player) > 21:
            await self._finish(interaction)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Parar", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction)

    @discord.ui.button(label="Dobrar", style=discord.ButtonStyle.success)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.player) != 2 or self.doubled:
            return await interaction.response.send_message("❌ Só no início.", ephemeral=True)
        wallet = get_wallet(get_bot_instance().db_conn, self.user_id)
        if wallet < self.aposta:
            return await interaction.response.send_message("💸 Sem saldo para dobrar.", ephemeral=True)
        modify_wallet(get_bot_instance().db_conn, self.user_id, -self.aposta)
        self.aposta *= 2
        self.doubled = True
        self.player.append(self.deck.pop())
        await self._finish(interaction)


@casino_group.command(name="blackjack", description="21 contra a banca.")
@app_commands.describe(aposta="Valor em Sachês")
async def blackjack(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    modify_wallet(get_bot_instance().db_conn, interaction.user.id, -aposta, interaction.user.display_name)
    deck = _new_deck()
    random.shuffle(deck)
    view = BlackjackView(interaction.user.id, aposta, deck)
    await interaction.response.send_message(embed=view._embed(), view=view)
    view.message = await interaction.original_response()


# --- CRASH ---

# House edge alvo: ~9% (faixa 8-10% "alto risco/recompensa" escolhida).
# Fórmula "provably fair" padrão de jogos crash: P(crash_point >= m) =
# (1 - house_edge) / m para m >= 1. Essa distribuição dá EV = -house_edge *
# aposta CONSTANTE não importa em que multiplicador o jogador decida sacar
# (diferente do range fixo antigo, que não tinha house edge configurável).
CRASH_HOUSE_EDGE = 0.09
# Teto prático do multiplicador: sem ele, um sorteio extremo de r muito
# pequeno geraria um crash_point gigantesco e a rodada rodaria por muito
# mais que os 120s de timeout da view (~0.6 de subida por segundo).
# Recorta só a cauda extrema (~1.8% de probabilidade com he=9%).
CRASH_MAX_MULTIPLIER = 30.0

# Cadência ALVO entre atualizações da mensagem.
#
# O ciclo é de TAXA fixa, não de atraso fixo: cada tick é agendado a partir
# do início da rodada (`inicio + n*TICK`), nunca a partir do fim da edição
# anterior. Um loop de atraso fixo ("edita, depois dorme 2s") soma a latência
# da edição ao intervalo — com edição de ~2s o jogador via 4s entre números.
# 1.5s = 3.33 edições por 5s, com folga sob o limite. Descer abaixo de ~1s
# encostaria no teto e passaria a render 429 — e um contador realmente fluido
# (vários quadros por segundo) é impossível: no Discord a mensagem só muda por
# requisição HTTP, então a cadência máxima é essa, não uma escolha de código.
CRASH_TICK_SECONDS = 1.5

# Crescimento EXPONENCIAL: multiplicador(t) = e^(k*t).
#
# Antes a subida era linear (~0.6x/s), e isso não conversava com a distribuição
# do crash_point, que se concentra embaixo: com house edge de 9% a mediana é
# 1.82x, que a 0.6x/s chegava em 1.4s. Ou seja, MAIS DA METADE das rodadas
# terminava antes do primeiro tick — o jogador via 1.00x e, em seguida, CRASH,
# sem nenhum número no meio. Era essa a sensação de "o número some".
#
# Exponencial dá passos pequenos no começo (1.00 → 1.09 → 1.18), que é onde
# quase toda rodada vive, e acelera depois. A rodada mediana passa a durar ~11s
# com ~7 atualizações. Não muda EV nem house edge: a distribuição do
# crash_point continua idêntica, só o tempo até chegar lá é outro.
#
# k=0.055 põe o teto de 30x em ~62s, dentro do timeout de 120s da view.
# Aumentar k deixa as rodadas mais rápidas e mais picotadas; diminuir, o
# contrário.
CRASH_GROWTH_K = 0.055


def _draw_crash_point(house_edge: float = CRASH_HOUSE_EDGE) -> float:
    r = random.uniform(1e-6, 1.0)
    crash_point = (1 - house_edge) / r
    return min(CRASH_MAX_MULTIPLIER, max(1.0, crash_point))


class CrashView(discord.ui.View):
    def __init__(self, user_id: int, aposta: int, crash_point: float):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.aposta = aposta
        self.crash_point = crash_point
        self.cashed_out = False
        # Sempre o último valor EFETIVAMENTE publicado na mensagem: é ele que
        # o jogador viu ao clicar, e é ele que o saque paga.
        self.multiplier = 1.0
        self.message: discord.Message | None = None
        # Fecha a rodada (saque OU crash). Serve para dois fins:
        # 1) o loop acorda na hora do saque em vez de dormir o tick inteiro;
        # 2) marca o fim ANTES de qualquer await, fechando a janela em que o
        #    botão ainda estava clicável depois do crash real.
        self.ended = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é seu jogo.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💰 Cash Out", style=discord.ButtonStyle.success)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ended.is_set():
            aviso = "❌ Já sacou." if self.cashed_out else "💥 Tarde demais — o avião já crashou."
            return await interaction.response.send_message(aviso, ephemeral=True)
        # Encerra a rodada de forma síncrona, antes do primeiro await: o loop
        # não chega a disparar mais nenhuma edição depois deste ponto.
        self.cashed_out = True
        self.ended.set()
        for child in self.children:
            child.disabled = True
        winnings = int(self.aposta * self.multiplier)
        # Responde ANTES de tocar no banco. `modify_wallet` faz BEGIN IMMEDIATE
        # síncrono; sob disputa de lock ele segura o event loop por até o
        # timeout do sqlite (5s por padrão) e estoura a janela de 3s que o
        # Discord dá para confirmar a interação — era daí que vinha parte do
        # "apertei e demorou para registrar".
        await interaction.response.edit_message(
            content=f"✅ Cash out em **{self.multiplier:.2f}x**! +{winnings} Sachês",
            view=self,
        )
        modify_wallet(get_bot_instance().db_conn, self.user_id, winnings)
        self.stop()


async def _crash_wait(view: CrashView, seconds: float) -> None:
    """Espera até `seconds`, mas acorda imediatamente se a rodada encerrar."""
    if seconds <= 0:
        return
    try:
        await asyncio.wait_for(view.ended.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _edit_multiplier(msg_obj, multiplier: float, medida: dict) -> None:
    """Publica um valor do multiplicador.

    Sem `view=`: os componentes não mudam entre ticks, então não há por que
    reenviá-los em toda requisição.

    Mede quanto a requisição demorou. Se ela custa mais que um tick, o teto do
    ritmo é a latência do Discord (rate limit ou rede), não a cadência daqui —
    e sem esse número o diagnóstico vira chute.
    """
    t0 = time.monotonic()
    try:
        await msg_obj.edit(content=f"🚀 Multiplicador: **{multiplier:.2f}x**")
    except discord.HTTPException:
        pass
    finally:
        medida["pior"] = max(medida["pior"], time.monotonic() - t0)
        medida["n"] += 1


async def _drenar(pendente) -> None:
    """Espera a edição em voo terminar antes de escrever o estado final —
    senão ela pode aterrissar DEPOIS e ressuscitar o avião na tela.
    """
    if pendente is None or pendente.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(pendente), timeout=10)
    except Exception:
        pass


# Uma rodada de crash por jogador. Sem isso, o mesmo usuário podia abrir
# várias rodadas em paralelo e cada uma somava requisições no mesmo bucket
# de canal (e no limite global do bot), recriando o travamento.
_crash_rounds_ativos: set[int] = set()


@casino_group.command(name="crash", description="Avião sobe até crashar — saque a tempo!")
@app_commands.describe(aposta="Valor em Sachês")
async def crash(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    if interaction.user.id in _crash_rounds_ativos:
        return await interaction.response.send_message(
            "⏳ Você já tem uma rodada de crash em andamento — termine ela primeiro.",
            ephemeral=True,
        )
    modify_wallet(get_bot_instance().db_conn, interaction.user.id, -aposta, interaction.user.display_name)

    await interaction.response.defer()
    crash_point = _draw_crash_point()
    view = CrashView(interaction.user.id, aposta, crash_point)
    # O multiplicador é função do relógio, não de incrementos acumulados: uma
    # edição lenta ou pulada não desloca mais a rodada nem o ponto de queda.
    crash_em = math.log(crash_point) / CRASH_GROWTH_K

    _crash_rounds_ativos.add(interaction.user.id)
    pendente = None
    medida = {"pior": 0.0, "n": 0}
    try:
        msg_obj = await interaction.followup.send(
            f"🚀 Avião decolando! Multiplicador: **{view.multiplier:.2f}x**",
            view=view,
            wait=True,
        )
        view.message = msg_obj
        inicio = time.monotonic()

        while not view.ended.is_set():
            agora = time.monotonic() - inicio
            if agora >= crash_em:
                break
            # Próxima fronteira de tick estritamente à frente, limitada ao
            # instante do crash. Garante progresso (nunca gira em falso) e
            # mantém o intervalo visível igual a CRASH_TICK_SECONDS.
            proximo = min((int(agora / CRASH_TICK_SECONDS) + 1) * CRASH_TICK_SECONDS, crash_em)
            await _crash_wait(view, proximo - agora)
            if view.ended.is_set() or proximo >= crash_em:
                break

            # Drop-behind: se a edição anterior ainda está em voo, PULA esta.
            # Nunca existe mais de uma requisição pendente, então a fila HTTP
            # não acumula backlog e o ritmo se auto-ajusta ao que o Discord
            # permite — em vez de o loop empurrar edições mais rápido do que
            # elas saem, que era o que fazia o número congelar e voltar.
            if pendente is not None and not pendente.done():
                continue
            # Só atualiza o valor pago depois de decidir publicá-lo: o saque
            # nunca paga um número que o jogador não chegou a ver.
            view.multiplier = math.exp(CRASH_GROWTH_K * proximo)
            pendente = asyncio.create_task(_edit_multiplier(msg_obj, view.multiplier, medida))

        await _drenar(pendente)

        if not view.cashed_out:
            # Marca o fim antes do await da edição final: durante essa janela
            # (que sob rate limit durava segundos) o botão continuava vivo e
            # pagava o multiplicador pré-crash.
            view.ended.set()
            for child in view.children:
                child.disabled = True
            try:
                await msg_obj.edit(
                    content=f"💥 **CRASH** em {crash_point:.2f}x! Você perdeu {aposta} Sachês.",
                    view=view,
                )
            except discord.HTTPException:
                pass
            view.stop()
    finally:
        _crash_rounds_ativos.discard(interaction.user.id)
        # Um aviso por rodada, e só quando a edição passa do tick: se aparecer,
        # o gargalo está na saída HTTP (rate limit/rede), não no agendamento.
        if medida["pior"] > CRASH_TICK_SECONDS:
            log_to_gui(
                f"Crash: edição mais lenta que o tick "
                f"({medida['pior']:.1f}s > {CRASH_TICK_SECONDS:.1f}s) "
                f"em {medida['n']} atualizações.",
                "WARNING",
            )


# --- SLOTS ---

@casino_group.command(name="slots", description="Caça-níqueis com 3 rolos.")
@app_commands.describe(aposta="Valor em Sachês")
async def slots(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)

    conn = get_bot_instance().db_conn
    uid = interaction.user.id
    # Escrow: debita a aposta ANTES de girar os rolos, não só depois da
    # animação (~3s de sleeps). Sem isso, o saldo fica "livre" durante toda
    # a janela de giro, permitindo múltiplas chamadas simultâneas passarem
    # pela checagem de saldo antes de qualquer uma debitar.
    modify_wallet(conn, uid, -aposta, interaction.user.display_name)

    await interaction.response.defer()
    symbols = ["🍒", "🍋", "🍊", "🍉", "🔔", "💎"]

    for _ in range(3):
        line = [random.choice(symbols) for _ in range(3)]
        embed = discord.Embed(
            title="🎰 SLOTS",
            description=f"| {' | '.join(line)} |\n\n*Girando...*",
            color=discord.Color.gold(),
        )
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1)

    # Tabela "alto risco" (RTP alvo ~80%, house edge ~20% — antes RTP≈40,3%,
    # mais punitivo que qualquer slot real). Com 6 símbolos equiprováveis e
    # 3 rolos independentes (216 combinações): 6 trincas (1 de cada símbolo),
    # 60 combinações de "2 de 3" e 150 sem prêmio.
    # RTP = (60*1 + 4*10 + 1*25 + 1*48) / 216 ≈ 80,1%.
    final = [random.choice(symbols) for _ in range(3)]
    premio = 0
    if final[0] == final[1] == final[2]:
        mult = {"💎": 48, "🔔": 25}.get(final[0], 10)
        premio = aposta * mult
    elif final[0] == final[1] or final[1] == final[2]:
        premio = aposta

    if premio:
        modify_wallet(conn, uid, premio)

    result = f"| {' | '.join(final)} |"
    if premio:
        desc = f"{result}\n\n🎉 **+{premio} Sachês!**"
    else:
        desc = f"{result}\n\n😢 Sem prêmio. -{aposta} Sachês"

    embed = discord.Embed(title="🎰 SLOTS — Resultado", description=desc, color=discord.Color.gold())
    await interaction.edit_original_response(embed=embed)


class CasinoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(casino_group)


async def setup(bot):
    await bot.add_cog(CasinoCog(bot))
