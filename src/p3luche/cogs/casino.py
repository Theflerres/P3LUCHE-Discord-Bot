"""
Casino P3LUCHE — poker, blackjack, crash e slots.

ATIVO: reativado em main.py após as correções da Fase 4 (escrow, bug do
Dobrar, overshoot do crash, house edge/RTP de crash e slots), todas
cobertas por tests/test_casino.py.
"""
from __future__ import annotations

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from config import get_bot_instance
from economy_db import get_wallet, modify_wallet

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

# Cadência de atualização da mensagem. O Discord limita edições de mensagem
# a ~5 requisições / 5s POR CANAL (em DM o bucket é o canal da DM). O valor
# antigo (0.5s) mandava 2 edições/s — 4x acima do limite — então o
# rate limiter do discord.py passava a dormir para respeitar os 429: o
# multiplicador congelava, edições atrasadas chegavam fora de ordem (o
# número "sumia"/voltava) e, com várias rodadas simultâneas, a fila HTTP
# global do bot atrasava até o clique do Cash Out demorar segundos para
# registrar. 2.0s deixa ~2.5x de folga no bucket do canal.
CRASH_TICK_SECONDS = 2.0
# Incremento por tick. Calibrado para manter a MESMA velocidade de subida
# por segundo de antes (uniform(0.1, 0.5) a cada 0.5s = 0.2-1.0 por segundo),
# de modo que a duração da rodada e a sensação do jogo não mudam — só a
# quantidade de requisições HTTP cai 4x.
CRASH_STEP_MIN = 0.4
CRASH_STEP_MAX = 2.0


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
        # não chega a disparar mais nenhuma edição que sobrescreveria a
        # confirmação do saque.
        self.cashed_out = True
        self.ended.set()
        for child in self.children:
            child.disabled = True
        winnings = int(self.aposta * self.multiplier)
        modify_wallet(get_bot_instance().db_conn, self.user_id, winnings)
        await interaction.response.edit_message(
            content=f"✅ Cash out em **{self.multiplier:.2f}x**! +{winnings} Sachês",
            view=self,
        )
        self.stop()


async def _crash_wait(view: CrashView, seconds: float) -> None:
    """Espera o próximo tick, mas acorda imediatamente se o jogador sacar."""
    try:
        await asyncio.wait_for(view.ended.wait(), timeout=seconds)
    except asyncio.TimeoutError:
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
    multiplier = 1.0
    _crash_rounds_ativos.add(interaction.user.id)
    try:
        msg_obj = await interaction.followup.send(
            f"🚀 Avião decolando! Multiplicador: **{multiplier:.2f}x**",
            view=view,
            wait=True,
        )
        view.message = msg_obj

        while not view.ended.is_set():
            proximo = multiplier + random.uniform(CRASH_STEP_MIN, CRASH_STEP_MAX)
            # Nunca deixa o multiplicador exposto/clicável passar do
            # crash_point: se o próximo incremento ultrapassaria, o crash já
            # aconteceu — quebra ANTES de publicar esse valor ou dar mais uma
            # janela de tick com o Cash Out ainda ativo além do ponto real de
            # queda.
            if proximo >= crash_point:
                break
            multiplier = proximo
            view.multiplier = multiplier
            # Recheca na borda do await: o saque pode ter entrado enquanto o
            # tick dormia, e uma edição em voo sobrescreveria a confirmação.
            if view.ended.is_set():
                break
            try:
                await msg_obj.edit(
                    content=f"🚀 Multiplicador: **{multiplier:.2f}x**",
                    view=view,
                )
            except discord.HTTPException:
                break
            await _crash_wait(view, CRASH_TICK_SECONDS)

        if not view.cashed_out:
            # Marca o fim antes do await da edição final: durante essa
            # janela (que sob rate limit podia durar segundos) o botão
            # continuava vivo e pagava o multiplicador pré-crash.
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
