"""
Minijogos de economia — craft, corrida, memória, batalha naval e leilão.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import get_bot_instance
from economy_db import (
    add_inventory_item,
    consume_fish,
    ensure_user,
    get_inventory,
    get_wallet,
    modify_wallet,
    set_inventory_item,
)
from cogs.economia import SHOP_ITEMS, eco_group

CRAFT_RECIPES = {
    "brilhante": {
        "peixes": {"Sardinha": 3},
        "scrap": 10,
        "result": "isca_brilhante",
    },
    "fedorenta": {
        "peixes": {"Bagre": 2},
        "scrap": 5,
        "result": "isca_fedorenta",
    },
    "eletrica": {
        "peixes": {"Enguia Elétrica": 1},
        "scrap": 15,
        "result": "isca_eletrica",
    },
}

FISH_TYPES = {
    "Água": ["Sardinha", "Tilápia", "Tubarão Branco", "Baleia Azul"],
    "Fogo": ["Piranha", "Baiacu", "Tubarão Martelo"],
    "Planta": ["Lambari", "Tambaqui", "Cavalo-Marinho"],
}

TYPE_WEAKNESS = {"Água": "Planta", "Planta": "Fogo", "Fogo": "Água"}


@eco_group.command(name="craftar", description="Cria iscas especiais com peixes e sucata.")
@app_commands.describe(tipo="brilhante, fedorenta ou eletrica")
async def craftar(interaction: discord.Interaction, tipo: str):
    tipo = tipo.lower().strip()
    if tipo not in CRAFT_RECIPES:
        return await interaction.response.send_message(
            "❌ Tipos válidos: `brilhante`, `fedorenta`, `eletrica`.",
            ephemeral=True,
        )

    recipe = CRAFT_RECIPES[tipo]
    conn = get_bot_instance().db_conn
    uid = interaction.user.id
    ensure_user(conn, uid, interaction.user.display_name)

    inv = get_inventory(conn, uid)
    row = conn.execute("SELECT scrap FROM users WHERE user_id = ?", (uid,)).fetchone()
    scrap = row["scrap"] if row else 0

    for fish, qty in recipe["peixes"].items():
        if inv.get(fish, 0) < qty:
            return await interaction.response.send_message(
                f"❌ Precisa de {qty}x **{fish}** (tem {inv.get(fish, 0)}).",
                ephemeral=True,
            )
    if scrap < recipe["scrap"]:
        return await interaction.response.send_message(
            f"❌ Precisa de {recipe['scrap']} sucata (tem {scrap}).",
            ephemeral=True,
        )

    for fish, qty in recipe["peixes"].items():
        consume_fish(conn, uid, fish, qty)

    conn.execute(
        "UPDATE users SET scrap = scrap - ? WHERE user_id = ?",
        (recipe["scrap"], uid),
    )
    add_inventory_item(conn, uid, recipe["result"], 1)
    conn.commit()

    item_name = SHOP_ITEMS.get(recipe["result"], {}).get("name", recipe["result"])
    await interaction.response.send_message(
        f"🔨 **Craft concluído!** +1 **{item_name}**",
        ephemeral=True,
    )


@eco_group.command(name="corrida", description="Aposte na corrida de peixes!")
@app_commands.describe(aposta="Sachês", peixe="Número do peixe (1-5)")
async def corrida(interaction: discord.Interaction, aposta: int, peixe: app_commands.Range[int, 1, 5]):
    if aposta < 10:
        return await interaction.response.send_message("❌ Aposta mínima: 10.", ephemeral=True)
    conn = get_bot_instance().db_conn
    uid = interaction.user.id
    if get_wallet(conn, uid) < aposta:
        return await interaction.response.send_message("💸 Saldo insuficiente.", ephemeral=True)

    modify_wallet(conn, uid, -aposta, interaction.user.display_name)
    await interaction.response.defer()

    racers = ["🐟", "🐠", "🐡", "🦑", "🐙"]
    progress = [0] * 5
    winner = None

    embed = discord.Embed(title="🏁 Corrida de Peixes!", color=discord.Color.blue())
    msg = await interaction.followup.send(embed=embed, wait=True)

    for _ in range(5):
        for i in range(5):
            progress[i] += random.randint(1, 3)
        lines = []
        for i, p in enumerate(progress):
            bar = "█" * min(p, 10) + "░" * max(0, 10 - p)
            marker = " 👈" if i + 1 == peixe else ""
            lines.append(f"{i + 1}. {racers[i]} `{bar}`{marker}")
        embed.description = "\n".join(lines)
        await msg.edit(embed=embed)
        if max(progress) >= 10 and winner is None:
            winner = progress.index(max(progress)) + 1
        await asyncio.sleep(1)

    if winner is None:
        winner = progress.index(max(progress)) + 1

    if winner == peixe:
        premio = aposta * 3
        modify_wallet(conn, uid, premio)
        result = f"🏆 Seu peixe **#{peixe}** venceu! +{premio} Sachês"
    else:
        result = f"😢 Peixe **#{winner}** venceu. Você apostou no #{peixe}. -{aposta} Sachês"

    embed.description = result
    embed.color = discord.Color.gold() if winner == peixe else discord.Color.red()
    await msg.edit(embed=embed)


class MemoriaView(discord.ui.View):
    def __init__(self, user_id: int, cartas: list[str]):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.cartas = cartas
        self.revealed = [False] * 12
        self.first_pick: int | None = None
        self.pairs_found = 0
        self.message: discord.Message | None = None

        for i in range(12):
            self.add_item(self._make_button(i))

    def _make_button(self, idx: int):
        view = self

        class CardButton(discord.ui.Button):
            def __init__(self):
                super().__init__(
                    label=str(idx + 1),
                    style=discord.ButtonStyle.secondary,
                    row=idx // 4,
                )
                self.idx = idx

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != view.user_id:
                    return await interaction.response.send_message("❌ Não é seu jogo.", ephemeral=True)
                if view.revealed[self.idx]:
                    return await interaction.response.send_message("❌ Já virada.", ephemeral=True)

                view.revealed[self.idx] = True
                if view.first_pick is None:
                    view.first_pick = self.idx
                    await interaction.response.edit_message(
                        content=view._board_text(), view=view
                    )
                else:
                    first = view.first_pick
                    if view.cartas[first] == view.cartas[self.idx]:
                        view.pairs_found += 1
                        view.first_pick = None
                        if view.pairs_found >= 6:
                            modify_wallet(
                                get_bot_instance().db_conn, view.user_id, 150
                            )
                            for child in view.children:
                                child.disabled = True
                            await interaction.response.edit_message(
                                content=view._board_text() + "\n\n🎉 **Vitória! +150 Sachês**",
                                view=view,
                            )
                            view.stop()
                            return
                    else:
                        await interaction.response.edit_message(
                            content=view._board_text(), view=view
                        )
                        await asyncio.sleep(1.5)
                        view.revealed[first] = False
                        view.revealed[self.idx] = False
                        view.first_pick = None
                        if view.message:
                            await view.message.edit(content=view._board_text(), view=view)
                        return
                    await interaction.response.edit_message(
                        content=view._board_text(), view=view
                    )

        return CardButton()

    def _board_text(self) -> str:
        lines = []
        for i in range(12):
            sym = self.cartas[i] if self.revealed[i] else "❓"
            lines.append(f"`{i + 1:2}` {sym}")
        rows = [" | ".join(lines[i : i + 4]) for i in range(0, 12, 4)]
        return "**🐠 Aquário Memória**\n" + "\n".join(rows)


@eco_group.command(name="memoria", description="Jogo da memória do aquário.")
async def memoria(interaction: discord.Interaction):
    pares = ["🐟", "🐠", "🐡", "🦑", "🐙", "🦀"]
    cartas = pares * 2
    random.shuffle(cartas)
    view = MemoriaView(interaction.user.id, cartas)
    await interaction.response.send_message(view._board_text(), view=view)
    view.message = await interaction.original_response()


class BattleView(discord.ui.View):
    def __init__(self, user_id: int, opponent_id: int, user_hp: int, opp_hp: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.opponent_id = opponent_id
        self.user_hp = user_hp
        self.opp_hp = opp_hp
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Não é sua batalha.", ephemeral=True)
            return False
        return True

    def _embed(self) -> discord.Embed:
        embed = discord.Embed(title="⚔️ Batalha Naval de Aquário", color=discord.Color.dark_blue())
        embed.add_field(name="Seu HP", value=str(self.user_hp), inline=True)
        embed.add_field(name="Oponente HP", value=str(self.opp_hp), inline=True)
        return embed

    async def _attack(self, interaction: discord.Interaction, atk_type: str):
        opp_type = random.choice(list(TYPE_WEAKNESS.keys()))
        dmg_user = 25 if TYPE_WEAKNESS.get(atk_type) == opp_type else 10
        dmg_opp = random.randint(8, 20)
        self.opp_hp -= dmg_user
        self.user_hp -= dmg_opp

        desc = f"Você usou **{atk_type}** vs **{opp_type}**!\n"
        desc += f"💥 -{dmg_user} no oponente | -{dmg_opp} em você"

        if self.opp_hp <= 0:
            modify_wallet(get_bot_instance().db_conn, self.user_id, 200)
            desc += "\n\n🏆 **Vitória! +200 Sachês**"
            for child in self.children:
                child.disabled = True
        elif self.user_hp <= 0:
            desc += "\n\n💀 **Derrota!**"
            for child in self.children:
                child.disabled = True

        embed = self._embed()
        embed.description = desc
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💧 Água", style=discord.ButtonStyle.primary)
    async def water(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._attack(interaction, "Água")

    @discord.ui.button(label="🔥 Fogo", style=discord.ButtonStyle.danger)
    async def fire(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._attack(interaction, "Fogo")

    @discord.ui.button(label="🌿 Planta", style=discord.ButtonStyle.success)
    async def plant(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._attack(interaction, "Planta")


@eco_group.command(name="batalhar", description="Batalha naval com peixes do inventário.")
async def batalhar(interaction: discord.Interaction, oponente: discord.Member):
    if oponente.bot:
        return await interaction.response.send_message("❌ Não pode batalhar com bots.", ephemeral=True)
    if oponente.id == interaction.user.id:
        return await interaction.response.send_message("❌ Não pode batalhar consigo.", ephemeral=True)

    conn = get_bot_instance().db_conn
    inv = get_inventory(conn, interaction.user.id)
    fish_count = sum(1 for k in inv if k in [f for fishes in FISH_TYPES.values() for f in fishes])
    if fish_count < 1:
        return await interaction.response.send_message(
            "❌ Você precisa de peixes no inventário.", ephemeral=True
        )

    view = BattleView(interaction.user.id, oponente.id, 100, 100)
    await interaction.response.send_message(embed=view._embed(), view=view)
    view.message = await interaction.original_response()


_active_auctions: dict[int, dict] = {}


class MinigamesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not self.auction_loop.is_running():
            self.auction_loop.start()

    def cog_unload(self):
        self.auction_loop.cancel()

    @tasks.loop(hours=12)
    async def auction_loop(self):
        await self.bot.wait_until_ready()
        item_key = random.choice(
            [k for k, v in SHOP_ITEMS.items() if v.get("price", 0) > 0]
        )
        item = SHOP_ITEMS[item_key]
        channel = None
        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
            if channel:
                break
        if not channel:
            return

        embed = discord.Embed(
            title="🔨 Leilão Secreto!",
            description=(
                f"Item: **{item['name']}**\n"
                f"Lance mínimo: **{max(50, item['price'] // 2)}** Sachês\n"
                f"⏰ Encerra em 1 hora!"
            ),
            color=discord.Color.purple(),
        )
        msg = await channel.send(embed=embed)
        ends = datetime.now() + timedelta(hours=1)
        _active_auctions[msg.id] = {
            "item_key": item_key,
            "highest": 0,
            "bidder": None,
            "ends": ends,
            "channel_id": channel.id,
        }
        await asyncio.sleep(3600)

        auction = _active_auctions.pop(msg.id, None)
        if not auction:
            return
        if auction["bidder"]:
            conn = self.bot.db_conn
            add_inventory_item(conn, auction["bidder"], auction["item_key"], 1)
            winner = await self.bot.fetch_user(auction["bidder"])
            await channel.send(
                f"🏆 Leilão encerrado! **{winner.mention}** venceu com "
                f"**{auction['highest']}** Sachês e recebeu **{item['name']}**!"
            )
        else:
            await channel.send("😢 Leilão encerrado sem lances.")

    @auction_loop.before_loop
    async def before_auction(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MinigamesCog(bot))
