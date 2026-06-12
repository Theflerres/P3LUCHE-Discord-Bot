"""
Casino P3LUCHE — poker, blackjack, crash e slots.
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
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é sua mesa.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
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
        for child in self.children:
            child.disabled = True

        p_score = _best_of_seven(self.player + self.community)
        b_score = _best_of_seven(self.bot_hand + self.community)
        conn = get_bot_instance().db_conn
        uid = interaction.user.id
        name = interaction.user.display_name

        if p_score > b_score:
            modify_wallet(conn, uid, self.aposta, name)
            result = f"🏆 Você venceu! +{self.aposta} Sachês\nMão: **{HAND_NAMES[p_score[0]]}**"
        elif p_score < b_score:
            modify_wallet(conn, uid, -self.aposta, name)
            result = f"💀 O bot venceu. -{self.aposta} Sachês\nMão do bot: **{HAND_NAMES[b_score[0]]}**"
        else:
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

        while _bj_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())

        p_val = _bj_value(self.player)
        d_val = _bj_value(self.dealer)
        conn = get_bot_instance().db_conn
        uid = self.user_id

        if timeout:
            msg = "⏰ Tempo esgotado — derrota automática."
            modify_wallet(conn, uid, -self.aposta)
        elif p_val > 21:
            msg = f"💥 Estourou ({p_val})! -{self.aposta} Sachês"
            modify_wallet(conn, uid, -self.aposta)
        elif d_val > 21 or p_val > d_val:
            win = self.aposta * 2 if not self.doubled else self.aposta
            modify_wallet(conn, uid, win)
            msg = f"🏆 Você venceu! +{win} Sachês"
        elif p_val == d_val:
            msg = "🤝 Empate!"
        else:
            modify_wallet(conn, uid, -self.aposta)
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

class CrashView(discord.ui.View):
    def __init__(self, user_id: int, aposta: int, crash_point: float):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.aposta = aposta
        self.crash_point = crash_point
        self.cashed_out = False
        self.multiplier = 1.0
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é seu jogo.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💰 Cash Out", style=discord.ButtonStyle.success)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cashed_out:
            return await interaction.response.send_message("❌ Já sacou.", ephemeral=True)
        self.cashed_out = True
        for child in self.children:
            child.disabled = True
        winnings = int(self.aposta * self.multiplier)
        modify_wallet(get_bot_instance().db_conn, self.user_id, winnings)
        await interaction.response.edit_message(
            content=f"✅ Cash out em **{self.multiplier:.2f}x**! +{winnings} Sachês",
            view=self,
        )
        self.stop()


@casino_group.command(name="crash", description="Avião sobe até crashar — saque a tempo!")
@app_commands.describe(aposta="Valor em Sachês")
async def crash(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    modify_wallet(get_bot_instance().db_conn, interaction.user.id, -aposta, interaction.user.display_name)

    await interaction.response.defer()
    crash_point = random.uniform(1.2, 10.0)
    view = CrashView(interaction.user.id, aposta, crash_point)
    multiplier = 1.0
    msg_obj = await interaction.followup.send(
        f"🚀 Avião decolando! Multiplicador: **{multiplier:.2f}x**",
        view=view,
        wait=True,
    )
    view.message = msg_obj

    while multiplier < crash_point and not view.cashed_out:
        multiplier += random.uniform(0.1, 0.5)
        view.multiplier = multiplier
        try:
            await msg_obj.edit(
                content=f"🚀 Multiplicador: **{multiplier:.2f}x**",
                view=view,
            )
        except discord.HTTPException:
            break
        await asyncio.sleep(0.5)

    if not view.cashed_out:
        for child in view.children:
            child.disabled = True
        await msg_obj.edit(
            content=f"💥 **CRASH** em {crash_point:.2f}x! Você perdeu {aposta} Sachês.",
            view=view,
        )


# --- SLOTS ---

@casino_group.command(name="slots", description="Caça-níqueis com 3 rolos.")
@app_commands.describe(aposta="Valor em Sachês")
async def slots(interaction: discord.Interaction, aposta: int):
    ok, msg = _check_bet(interaction, aposta)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)

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

    final = [random.choice(symbols) for _ in range(3)]
    premio = 0
    if final[0] == final[1] == final[2]:
        mult = {"💎": 10, "🔔": 5}.get(final[0], 3)
        premio = aposta * mult
    elif final[0] == final[1] or final[1] == final[2]:
        premio = aposta

    conn = get_bot_instance().db_conn
    uid = interaction.user.id
    modify_wallet(conn, uid, -aposta, interaction.user.display_name)
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
