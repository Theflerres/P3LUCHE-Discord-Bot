"""
Ilha pessoal (Fase 6) — hub /ilha: progressão linear por tier, construções
com custo em Sachê+sucata e tempo de construção (máquina de estados
idle/building/ready, mesmo padrão do covo do Galdino em cogs/economia.py).

Distinta da "Ilha do Náufrago" (WORLD_LORE["island"] em cogs/economia.py):
aquela é a localidade de lore/exploração do drone (/eco explorar). Esta é a
ilha privada do jogador — conceitos separados por decisão de design.

Nenhum benefício mecânico ou cosmético de construção é implementado ainda
(pendente de decisão) — apenas existência, custo e desbloqueio de tier.
Catálogo (nomes, tema e valores) definido pelo dono: tema náufrago/
sobrevivência pessoal.
"""
from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import get_bot_instance
from utils import get_local_file
from economy_db import (
    ensure_user,
    finalize_island_construction,
    get_island,
    get_island_structures,
    get_scrap,
    get_wallet,
    start_island_construction,
)

# --- LORE DO HUB (distinta de WORLD_LORE["island"], a Ilha do Náufrago) ---
ISLAND_HUB_LORE = {
    "title": "🏝️ Ilha Pessoal",
    "description": (
        "Seu próprio pedaço de terra, longe da Ilha do Náufrago da lore "
        "principal. Aqui a sobrevivência é por sua conta: Sachê, sucata e "
        "esforço próprio."
    ),
}

# --- CATÁLOGO DE CONSTRUÇÕES (tema náufrago/sobrevivência pessoal) ---
# "nucleo" é a estrutura-núcleo: evoluí-la sobe o tier geral da ilha
# (progressão linear). As demais só ficam disponíveis a partir do tier
# indicado em "unlock_tier" e, nesta fase, têm nível único (construir = feito,
# sem upgrade) — apenas existência, sem benefício mecânico associado (ainda
# pendente de decisão, não é placeholder de nome/tema).
ISLAND_STRUCTURES = {
    "nucleo": {
        "name": "Acampamento",
        "desc": "O centro da sua ilha pessoal. Evoluí-lo aumenta o tier da ilha e libera novas construções.",
        "is_core": True,
        "unlock_tier": 0,
        "max_level": 4,
        "cost_saches_per_level": 1500,
        "cost_scrap_per_level": 150,
        "build_hours": 2,
    },
    "deposito": {
        "name": "Baú da Maré",
        "desc": "Onde você guarda o que a maré traz. Ainda sem benefício mecânico associado.",
        "is_core": False,
        "unlock_tier": 1,
        "max_level": 1,
        "cost_saches": 2400,
        "cost_scrap": 240,
        "build_hours": 3,
    },
    "oficina": {
        "name": "Bancada do Náufrago",
        "desc": "Uma bancada de trabalho improvisada. Ainda sem benefício mecânico associado.",
        "is_core": False,
        "unlock_tier": 2,
        "max_level": 1,
        "cost_saches": 4500,
        "cost_scrap": 450,
        "build_hours": 5,
    },
    "farol": {
        "name": "Farol Pessoal",
        "desc": "Um farol erguido à mão na ponta da ilha. Ainda sem benefício mecânico associado.",
        "is_core": False,
        "unlock_tier": 3,
        "max_level": 1,
        "cost_saches": 7500,
        "cost_scrap": 750,
        "build_hours": 8,
    },
}


# --- BÔNUS MECÂNICOS DAS CONSTRUÇÕES ---
# Cada estrutura mexe num eixo diferente de propósito. Quatro construções
# dando "+X% de renda" seriam a mesma construção quatro vezes, e a decisão de
# qual erguer primeiro deixaria de existir.
#
# O núcleo escala com o nível (é a única com mais de um); as demais têm nível
# único, então o bônus liga quando ela existe.
ISLAND_BONUS = {
    "nucleo_cd_por_nivel": 0.02,   # -2% de cooldown de pesca por nível (máx -8%)
    "deposito_sucata_mult": 1.25,  # +25% em toda fonte de sucata
    "oficina_craft_mult": 0.5,     # craft custa metade da sucata
    "farol_sorte": 0.10,           # +10% de sorte, multiplicativo com a vara
}


def island_bonuses(structures: dict) -> dict:
    """Bônus ativos a partir do estado das construções.

    Função pura sobre o dicionário de `get_island_structures` para poder ser
    exercitada sem banco. Construção em obra (`status == 'building'`) ainda
    não conta: o nível só sobe em `finalize_island_construction`.
    """
    def nivel(chave):
        estado = structures.get(chave)
        return estado["level"] if estado else 0

    nucleo = min(nivel("nucleo"), ISLAND_STRUCTURES["nucleo"]["max_level"])
    return {
        "cd_reducao": nucleo * ISLAND_BONUS["nucleo_cd_por_nivel"],
        "sucata_mult": ISLAND_BONUS["deposito_sucata_mult"] if nivel("deposito") else 1.0,
        "craft_mult": ISLAND_BONUS["oficina_craft_mult"] if nivel("oficina") else 1.0,
        "sorte_bonus": ISLAND_BONUS["farol_sorte"] if nivel("farol") else 0.0,
        "isca_diaria": bool(nivel("oficina")),
    }


def get_island_bonuses(conn, user_id: int) -> dict:
    """Versão que lê do banco. Ponto único de consulta para os outros cogs —
    nenhum deles deve reimplementar a leitura das estruturas."""
    return island_bonuses(get_island_structures(conn, user_id))


def _structure_cost(structure_key: str, current_level: int) -> dict:
    """Custo/tempo para levar `structure_key` de current_level para o
    próximo nível. Para estruturas-núcleo o custo escala com o nível alvo
    (mesmo padrão de try_upgrade_rod); para as demais (max_level=1) é fixo.
    """
    stats = ISLAND_STRUCTURES[structure_key]
    target_level = current_level + 1
    if stats["is_core"]:
        cost_saches = stats["cost_saches_per_level"] * target_level
        cost_scrap = stats["cost_scrap_per_level"] * target_level
    else:
        cost_saches = stats["cost_saches"]
        cost_scrap = stats["cost_scrap"]
    return {
        "target_level": target_level,
        "cost_saches": cost_saches,
        "cost_scrap": cost_scrap,
        "build_hours": stats["build_hours"],
    }


def _structure_button_spec(structure_key: str, island_tier: int, struct_state: dict | None, now_ts: float) -> dict:
    """Decide label/estilo/disabled do botão desta construção a partir do
    estado atual — só apresentação, não decide nada de custo/regra aqui
    além do que já foi calculado em _structure_cost."""
    stats = ISLAND_STRUCTURES[structure_key]
    level = struct_state["level"] if struct_state else 0
    status = struct_state["status"] if struct_state else "idle"
    timer_end = struct_state["timer_end"] if struct_state else None

    if stats["unlock_tier"] > island_tier:
        return {
            "label": f"🔒 {stats['name']} (Tier {stats['unlock_tier']}+)",
            "style": discord.ButtonStyle.secondary,
            "disabled": True,
        }

    if status == "building":
        if timer_end and now_ts >= timer_end:
            return {
                "label": f"✅ Coletar: {stats['name']}",
                "style": discord.ButtonStyle.success,
                "disabled": False,
            }
        return {
            "label": f"⏳ {stats['name']} (construindo)",
            "style": discord.ButtonStyle.secondary,
            "disabled": True,
        }

    if level >= stats["max_level"]:
        label = "Nível Máximo" if stats["is_core"] else "Construído"
        return {
            "label": f"✅ {label}: {stats['name']}",
            "style": discord.ButtonStyle.secondary,
            "disabled": True,
        }

    cost = _structure_cost(structure_key, level)
    verb = "Evoluir" if level > 0 else "Construir"
    return {
        "label": f"{verb}: {stats['name']} ({cost['cost_saches']}$ / {cost['cost_scrap']}⚙️)",
        "style": discord.ButtonStyle.primary,
        "disabled": False,
    }


def build_island_embed(user_name: str, island: dict, structures: dict, wallet: int, scrap: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{ISLAND_HUB_LORE['title']} — Tier {island['tier']}",
        description=ISLAND_HUB_LORE["description"],
        color=discord.Color.teal(),
    )
    embed.add_field(name="Recursos", value=f"💰 {wallet} Sachês\n⚙️ {scrap} Sucata", inline=True)

    now_ts = datetime.now().timestamp()
    lines = []
    for key, stats in ISLAND_STRUCTURES.items():
        state = structures.get(key)
        level = state["level"] if state else 0
        status = state["status"] if state else "idle"
        if stats["unlock_tier"] > island["tier"]:
            status_text = f"🔒 Bloqueado (Tier {stats['unlock_tier']}+)"
        elif status == "building":
            timer_end = state["timer_end"] if state else None
            if timer_end and now_ts >= timer_end:
                status_text = "✅ Pronta para coletar"
            else:
                status_text = f"⏳ Construindo (<t:{int(timer_end)}:R>)" if timer_end else "⏳ Construindo"
        elif level >= stats["max_level"]:
            status_text = f"✅ Nível {level}/{stats['max_level']}"
        elif level > 0:
            status_text = f"🔧 Nível {level}/{stats['max_level']} (pode evoluir)"
        else:
            status_text = "◻️ Não construída"
        lines.append(f"**{stats['name']}** — {status_text}")

    embed.add_field(name="Construções", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Ilha de {user_name} • Fase 6: sem benefícios mecânicos ainda (pendente de decisão)")
    return embed


class IlhaHubView(discord.ui.View):
    """Hub da ilha pessoal — um botão por construção do catálogo, no molde
    de CityHubView/GaldinoView já existentes em cogs/economia.py."""

    def __init__(self, user_id: int, user_name: str, island: dict, structures: dict):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.user_name = user_name

        now_ts = datetime.now().timestamp()
        for key in ISLAND_STRUCTURES:
            state = structures.get(key)
            spec = _structure_button_spec(key, island["tier"], state, now_ts)
            btn = discord.ui.Button(
                label=spec["label"][:80],
                style=spec["style"],
                disabled=spec["disabled"],
            )

            async def callback(interaction: discord.Interaction, structure_key=key):
                await self._handle_structure_click(interaction, structure_key)

            btn.callback = callback
            self.add_item(btn)

    async def _handle_structure_click(self, interaction: discord.Interaction, structure_key: str):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Essa não é a sua ilha.", ephemeral=True)

        conn = get_bot_instance().db_conn
        stats = ISLAND_STRUCTURES[structure_key]
        island = get_island(conn, self.user_id)
        structures = get_island_structures(conn, self.user_id)
        state = structures.get(structure_key)
        level = state["level"] if state else 0
        status = state["status"] if state else "idle"
        now_ts = datetime.now().timestamp()

        if stats["unlock_tier"] > island["tier"]:
            return await interaction.response.send_message(
                f"🔒 Requer Tier {stats['unlock_tier']} da ilha (atual: {island['tier']}).", ephemeral=True
            )

        if status == "building":
            timer_end = state["timer_end"] if state else None
            if not timer_end or now_ts < timer_end:
                eta = f"<t:{int(timer_end)}:R>" if timer_end else "em breve"
                return await interaction.response.send_message(f"⏳ Ainda construindo. Pronta {eta}.", ephemeral=True)

            target_level = level + 1
            result = finalize_island_construction(conn, self.user_id, structure_key, target_level, stats["is_core"])
            if not result["success"]:
                return await interaction.response.send_message("⏳ Ainda não está pronta.", ephemeral=True)

            msg = f"✅ **{stats['name']}** concluída! Nível {result['level']}/{stats['max_level']}."
            if result.get("tier") is not None:
                msg += f"\n🏝️ **A ilha subiu para o Tier {result['tier']}!**"
            return await interaction.response.send_message(msg, ephemeral=True)

        # status idle: tentar iniciar construção/upgrade
        if level >= stats["max_level"]:
            label = "nível máximo" if stats["is_core"] else "já construída"
            return await interaction.response.send_message(f"✅ **{stats['name']}** está no {label}.", ephemeral=True)

        cost = _structure_cost(structure_key, level)
        result = start_island_construction(
            conn,
            self.user_id,
            structure_key,
            cost["target_level"],
            cost["cost_saches"],
            cost["cost_scrap"],
            cost["build_hours"],
            required_tier=stats["unlock_tier"],
        )
        if not result["success"]:
            if result["reason"] == "locked":
                return await interaction.response.send_message(
                    f"🔒 Requer Tier {stats['unlock_tier']} da ilha (atual: {result['tier']}).", ephemeral=True
                )
            if result["reason"] == "insufficient_resources":
                return await interaction.response.send_message(
                    f"💸 Faltam recursos: precisa de {cost['cost_saches']} Sachês / {cost['cost_scrap']} sucata "
                    f"(tem {result['wallet']} / {result['scrap']}).",
                    ephemeral=True,
                )
            if result["reason"] == "already_building":
                return await interaction.response.send_message("⏳ Já está em construção.", ephemeral=True)
            if result["reason"] == "pending_collect":
                return await interaction.response.send_message("✅ Já está pronta — colete primeiro.", ephemeral=True)
            return await interaction.response.send_message("❌ Não foi possível iniciar agora.", ephemeral=True)

        ready_ts = int(result["timer_end"])
        verb = "Evolução" if level > 0 else "Construção"
        await interaction.response.send_message(
            f"🛠️ **{verb} de {stats['name']} iniciada!** Pronta <t:{ready_ts}:R>.", ephemeral=True
        )


class IlhaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ilha", description="Sua ilha pessoal: tier, recursos e construções.")
    async def ilha(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        conn = get_bot_instance().db_conn
        ensure_user(conn, user_id, interaction.user.name)

        island = get_island(conn, user_id)
        structures = get_island_structures(conn, user_id)
        wallet = get_wallet(conn, user_id)
        scrap = get_scrap(conn, user_id)

        embed = build_island_embed(interaction.user.name, island, structures, wallet, scrap)
        view = IlhaHubView(user_id, interaction.user.name, island, structures)

        # Placeholder de imagem — segue o mesmo padrão gracioso de get_local_file
        # já usado em todos os outros hubs (guilda/taverna/oficina): se o
        # arquivo não existir ainda, o embed sai sem imagem, sem erro.
        file, url = get_local_file("assets/locais/ilha_pessoal.jpg", "ilha_pessoal.jpg")
        if file:
            embed.set_image(url=url)
            await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(IlhaCog(bot))
