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
    get_cooldowns,
    get_inventory,
    get_wallet,
    modify_scrap,
    modify_wallet,
    set_cooldown,
    set_inventory_item,
)
from cogs.economia import SHOP_ITEMS, eco_group
from cogs.ilha import get_island_bonuses

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
APPROVAL_CHANNEL_ID = 1176206157380079718
AUCTION_CHANNEL_ID = 1453842140030435458
APPROVAL_TIMEOUT_SECONDS = 300
BOT_OWNER_ID = 299323165937500160


def _get_auction_min_bid(item: dict) -> int:
    rarity = item.get("rarity", "common")
    rarity_multipliers = {
        "common": 1.0,
        "rare": 1.5,
        "epic": 2.0,
        "legendary": 3.0,
        "mythic": 4.0,
    }
    return max(50, int(item.get("price", 0) * rarity_multipliers.get(rarity, 1.0) // 2))


def build_auction_start_message(item_name: str) -> str:
    return f"🎉 NOVO LEILÃO INICIADO! O item de hoje é **{item_name}**"


def build_auction_approval_embed(item_name: str, rarity: str, min_bid: int) -> discord.Embed:
    embed = discord.Embed(
        title="📝 Solicitação de Leilão",
        description=(
            f"Um novo leilão foi solicitado para **{item_name}**.\n\n"
            f"✨ Raridade: **{rarity.title()}**\n"
            f"💸 Lance mínimo: **{min_bid}** Sachês\n\n"
            f"⏳ Você tem **5 minutos** para **aprovar** ou **recusar** esta solicitação."
        ),
        color=discord.Color.orange(),
    )
    return embed


def format_time_remaining(total_seconds: int) -> str:
    remaining = max(0, total_seconds)
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s"


def build_auction_embed(item_name: str, rarity: str, min_bid: int, ends_at: datetime | None = None, participant: str | None = None, last_bid: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="🔨 Leilão Secreto!",
        description=(
            f"Item: **{item_name}**\n"
            f"Raridade: **{rarity.title()}**\n"
            f"Lance mínimo: **{min_bid}** Sachês"
        ),
        color=discord.Color.purple(),
    )
    if ends_at is not None:
        embed.add_field(name="⏳ Tempo restante", value=format_time_remaining(int((ends_at - datetime.now()).total_seconds())), inline=False)
    embed.add_field(name="👤 Participante atual", value=participant or "Nenhum", inline=True)
    embed.add_field(name="💸 Último lance", value=f"{last_bid} Sachês" if last_bid is not None else "Nenhum", inline=True)
    return embed


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
    # Bancada do Náufrago (ilha) corta o custo de sucata pela metade. O custo
    # cobrado é o mesmo que a checagem acima usa — calcular duas vezes com
    # regras diferentes deixaria o jogador ser recusado por um valor e
    # debitado por outro.
    custo_scrap = int(recipe["scrap"] * get_island_bonuses(conn, uid)["craft_mult"])
    if scrap < custo_scrap:
        return await interaction.response.send_message(
            f"❌ Precisa de {custo_scrap} sucata (tem {scrap}).",
            ephemeral=True,
        )

    for fish, qty in recipe["peixes"].items():
        consume_fish(conn, uid, fish, qty)

    # modify_scrap() em vez de UPDATE cru: o SQL direto na v4 ficava pendente
    # sem sincronizar para a legada, e o ensure_user() de add_inventory_item()
    # logo abaixo disparava sync_user_from_economy(), que sobrescrevia
    # users.scrap com o valor antigo vindo de `economy` — o craft saía de
    # graça. Precisa vir ANTES do add_inventory_item() do resultado.
    modify_scrap(conn, uid, -custo_scrap)
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
        self.finished = False
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
                # Guarda anti-replay (mesmo padrão de BlackjackView/CrashView):
                # reforça a proteção já existente via `revealed` contra
                # pagamento duplo se a vitória for processada mais de uma vez.
                if view.finished:
                    return await interaction.response.send_message("❌ Jogo já encerrado.", ephemeral=True)
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
                            view.finished = True
                            modify_wallet(
                                get_bot_instance().db_conn, view.user_id, MEMORIA_PREMIO
                            )
                            for child in view.children:
                                child.disabled = True
                            await interaction.response.edit_message(
                                content=view._board_text()
                                + f"\n\n🎉 **Vitória! +{MEMORIA_PREMIO} Sachês**",
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


# 300 Sachês garantidos por vitória mereciam custo real de entrada — sem
# isso, o jogo pagava 20-48k Sachês/hora de graça (auditoria da Fase 4).
#
# 300s -> 1800s (Fase 3). Enquanto o cooldown era o mesmo da vara inicial, o
# Aquário era um SUBSTITUTO da pescaria: mesma cadência, mesmo clique, sem
# equipamento e sem risco. Meia hora o põe numa cadência própria (6x o
# cooldown base da vara) — é uma escolha de renda passiva com espera, não
# uma segunda pescaria rodando em paralelo.
MEMORIA_COOLDOWN_SECONDS = 1800

# 150 -> 100 -> 140, junto com o cooldown de 5 para 30 minutos (Fase 3).
#
# O prêmio SUBIU e mesmo assim o Aquário ficou 4,3x mais barato para a
# economia, porque o que governa a renda é o par (prêmio, cooldown), não o
# prêmio sozinho:
#
#   antes:  100 x 12 usos/h = 1.200/h  (4,9x a pesca de entrada)
#   agora:  140 x  2 usos/h =   280/h  (1,14x a pesca de entrada)
#
# A referência é a Vara de Bambu pós-Fase 1: 20,39 Sachês por lance com
# cooldown de 5 min, ou seja 244,7/h. O alvo era ficar POUCO acima dela —
# o Aquário deve valer a pena, mas não a ponto de a vara virar decoração.
# 280/h é +14,4%.
#
# Subir o prêmio junto com o cooldown é deliberado: a rodada individual tem
# que parecer melhor do que era, senão a mudança lê como punição pura. O que
# encolheu foi a frequência, que é onde estava o problema.
MEMORIA_PREMIO = 140


@eco_group.command(name="memoria", description="Jogo da memória do aquário.")
async def memoria(interaction: discord.Interaction):
    conn = get_bot_instance().db_conn
    uid = interaction.user.id
    ensure_user(conn, uid, interaction.user.display_name)

    agora = datetime.now()
    last_memoria = get_cooldowns(conn, uid)["last_memoria"]
    if last_memoria:
        try:
            last_dt = datetime.strptime(last_memoria, "%Y-%m-%d %H:%M:%S.%f")
            elapsed = (agora - last_dt).total_seconds()
            if elapsed < MEMORIA_COOLDOWN_SECONDS:
                wait = int(MEMORIA_COOLDOWN_SECONDS - elapsed)
                ts = int((agora + timedelta(seconds=wait)).timestamp())
                return await interaction.response.send_message(
                    f"⏳ **Aquário Memória:** As cartas ainda estão sendo embaralhadas... Volte <t:{ts}:R>.",
                    ephemeral=True,
                )
        except ValueError:
            pass

    # Reserva o cooldown ANTES de abrir o tabuleiro (mesmo padrão de
    # pescar/explorar) — sem isso, duas chamadas em sequência rápida
    # passariam pela checagem antes de qualquer uma reservar.
    set_cooldown(conn, uid, "last_memoria", agora.strftime("%Y-%m-%d %H:%M:%S.%f"))

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
        self.finished = False
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
        # Guarda anti-replay (mesmo padrão de BlackjackView/CrashView):
        # fecha o pagamento duplo por clique duplo no botão vencedor.
        if self.finished:
            return await interaction.response.send_message("❌ Batalha já encerrada.", ephemeral=True)

        opp_type = random.choice(list(TYPE_WEAKNESS.keys()))
        dmg_user = 25 if TYPE_WEAKNESS.get(atk_type) == opp_type else 10
        dmg_opp = random.randint(8, 20)
        self.opp_hp -= dmg_user
        self.user_hp -= dmg_opp

        desc = f"Você usou **{atk_type}** vs **{opp_type}**!\n"
        desc += f"💥 -{dmg_user} no oponente | -{dmg_opp} em você"

        if self.opp_hp <= 0:
            self.finished = True
            modify_wallet(get_bot_instance().db_conn, self.user_id, 200)
            desc += "\n\n🏆 **Vitória! +200 Sachês**"
            for child in self.children:
                child.disabled = True
        elif self.user_hp <= 0:
            self.finished = True
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
    battle_fish = [f for fishes in FISH_TYPES.values() for f in fishes]
    # O gate de "precisa de peixe" nunca era exercido: checava a presença
    # mas não consumia nada (auditoria da Fase 4). Consome de fato 1 peixe
    # válido antes de abrir a batalha — mesma isca gasta em pescar/craftar.
    custo_peixe = next((f for f in battle_fish if inv.get(f, 0) > 0), None)
    if not custo_peixe:
        return await interaction.response.send_message(
            "❌ Você precisa de peixes no inventário.", ephemeral=True
        )
    consume_fish(conn, interaction.user.id, custo_peixe, 1)

    view = BattleView(interaction.user.id, oponente.id, 100, 100)
    embed = view._embed()
    embed.set_footer(text=f"🐟 Peixe usado como isca de combate: {custo_peixe}")
    await interaction.response.send_message(embed=embed, view=view)
    view.message = await interaction.original_response()


_active_auctions: dict[int, dict] = {}


class AuctionView(discord.ui.View):
    def __init__(self, auction_id: int, item_key: str, item_name: str, min_bid: int, ends_at: datetime):
        super().__init__(timeout=None)
        self.auction_id = auction_id
        self.item_key = item_key
        self.item_name = item_name
        self.min_bid = min_bid
        self.ends_at = ends_at

    @discord.ui.button(label="Dar Lance", style=discord.ButtonStyle.primary, emoji="💸")
    async def place_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        auction = _active_auctions.get(self.auction_id)
        if not auction:
            return await interaction.response.send_message("❌ Este leilão já encerrou.", ephemeral=True)
        if self.ends_at <= datetime.now():
            _active_auctions.pop(self.auction_id, None)
            return await interaction.response.send_message("❌ Este leilão já encerrou.", ephemeral=True)

        conn = get_bot_instance().db_conn
        bidder_id = interaction.user.id
        bidder_name = interaction.user.display_name
        ensure_user(conn, bidder_id, bidder_name)

        wallet = get_wallet(conn, bidder_id)
        current_highest = auction.get("highest", 0)
        suggested = max(self.min_bid, current_highest + max(10, self.min_bid // 10)) if current_highest > 0 else self.min_bid
        if wallet < suggested:
            return await interaction.response.send_message(
                f"💸 Você precisa de pelo menos {suggested} Sachês para dar este lance.",
                ephemeral=True,
            )

        previous_bidder = auction.get("bidder")
        previous_highest = auction.get("highest", 0)
        if previous_bidder and previous_bidder != bidder_id:
            modify_wallet(conn, previous_bidder, previous_highest, auction.get("bidder_name", ""))

        modify_wallet(conn, bidder_id, -suggested, bidder_name)
        auction["highest"] = suggested
        auction["bidder"] = bidder_id
        auction["bidder_name"] = bidder_name
        auction["last_message"] = interaction.message.id

        embed = build_auction_embed(
            self.item_name,
            auction.get("rarity", "common"),
            auction.get("min_bid", self.min_bid),
            auction.get("ends"),
            bidder_name,
            suggested,
        )
        await interaction.message.edit(embed=embed)

        await interaction.response.send_message(
            f"✅ Lance de **{suggested} Sachês** aceito para **{self.item_name}**.",
            ephemeral=True,
        )


class AuctionApprovalView(discord.ui.View):
    def __init__(self, bot, item_key: str, item_name: str, min_bid: int, rarity: str):
        super().__init__(timeout=APPROVAL_TIMEOUT_SECONDS)
        self.bot = bot
        self.item_key = item_key
        self.item_name = item_name
        self.min_bid = min_bid
        self.rarity = rarity
        self.message: discord.Message | None = None
        self.approval_completed = asyncio.Event()
        self.approved = False

    async def _is_approver(self, user: discord.User) -> bool:
        return user.id == BOT_OWNER_ID

    async def _start_auction(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Leilão aprovado. Iniciando...", ephemeral=True)
        self.approved = True
        self.approval_completed.set()
        await self.bot.get_cog("MinigamesCog")._launch_auction(
            self.item_key,
            self.item_name,
            self.min_bid,
            self.rarity,
        )

    async def _reject_auction(self, interaction: discord.Interaction):
        self.approval_completed.set()
        if self.message:
            await self.message.edit(content="❌ Leilão recusado pelo aprovador.", view=None, embed=None)
        await interaction.response.send_message("❌ Leilão recusado.", ephemeral=True)

    @discord.ui.button(label="✅ Aprovar", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._is_approver(interaction.user):
            return await interaction.response.send_message("❌ Apenas o dono do bot pode aprovar ou recusar este leilão.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(content="✅ Leilão aprovado! Iniciando no canal de jogadores...", view=self)
        await self._start_auction(interaction)

    @discord.ui.button(label="❌ Recusar", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._is_approver(interaction.user):
            return await interaction.response.send_message("❌ Apenas o dono do bot pode aprovar ou recusar este leilão.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await self._reject_auction(interaction)

    async def on_timeout(self) -> None:
        self.approval_completed.set()
        if self.message:
            await self.message.edit(content="⏰ Tempo de aprovação encerrado. Leilão não iniciado.", view=None, embed=None)


class MinigamesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not self.auction_loop.is_running():
            self.auction_loop.start()

    def cog_unload(self):
        self.auction_loop.cancel()

    async def _launch_auction(self, item_key: str, item_name: str, min_bid: int, rarity: str):
        item = SHOP_ITEMS[item_key]
        channel = self.bot.get_channel(AUCTION_CHANNEL_ID)
        if not channel:
            channel = await self.bot.fetch_channel(AUCTION_CHANNEL_ID)

        ends_at = datetime.now() + timedelta(hours=1)
        embed = build_auction_embed(item_name, rarity, min_bid, ends_at)
        view = AuctionView(0, item_key, item_name, min_bid, datetime.now() + timedelta(hours=1))
        content = build_auction_start_message(item_name)
        msg = await channel.send(content=content, embed=embed, view=view)
        ends = datetime.now() + timedelta(hours=1)
        _active_auctions[msg.id] = {
            "item_key": item_key,
            "highest": 0,
            "bidder": None,
            "bidder_name": None,
            "ends": ends,
            "channel_id": channel.id,
            "item_name": item_name,
            "min_bid": min_bid,
            "rarity": rarity,
            "message_id": msg.id,
        }
        view.auction_id = msg.id
        await msg.edit(view=view)

        auction = None
        try:
            while True:
                auction = _active_auctions.get(msg.id)
                if not auction:
                    break
                if datetime.now() >= auction["ends"]:
                    break
                try:
                    await msg.edit(embed=build_auction_embed(
                        auction["item_name"],
                        auction.get("rarity", "common"),
                        auction.get("min_bid", min_bid),
                        auction["ends"],
                        auction.get("bidder_name"),
                        auction.get("highest"),
                    ))
                except discord.NotFound:
                    break
                await asyncio.sleep(10)
        finally:
            auction = _active_auctions.pop(msg.id, None)

        if not auction:
            return
        if auction["bidder"]:
            conn = self.bot.db_conn
            add_inventory_item(conn, auction["bidder"], auction["item_key"], 1)
            winner = await self.bot.fetch_user(auction["bidder"])
            await channel.send(
                f"🏆 Leilão encerrado! **{winner.mention}** venceu com "
                f"**{auction['highest']}** Sachês e recebeu **{item_name}**!"
            )
        else:
            await channel.send("😢 Leilão encerrado sem lances.")

    @tasks.loop(hours=12)
    async def auction_loop(self):
        await self.bot.wait_until_ready()
        item_key = random.choice(
            [k for k, v in SHOP_ITEMS.items() if v.get("price", 0) > 0]
        )
        item = SHOP_ITEMS[item_key]
        min_bid = _get_auction_min_bid(item)
        approval_channel = self.bot.get_channel(APPROVAL_CHANNEL_ID)
        if not approval_channel:
            approval_channel = await self.bot.fetch_channel(APPROVAL_CHANNEL_ID)

        if not approval_channel:
            return

        embed = build_auction_approval_embed(item['name'], item.get('rarity', 'common'), min_bid)
        view = AuctionApprovalView(self.bot, item_key, item['name'], min_bid, item.get('rarity', 'common'))
        msg = await approval_channel.send(embed=embed, view=view)
        view.message = msg
        await view.approval_completed.wait()
        if not view.approved:
            return

    @auction_loop.before_loop
    async def before_auction(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MinigamesCog(bot))
