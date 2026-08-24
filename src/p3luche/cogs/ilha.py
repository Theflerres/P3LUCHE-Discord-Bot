"""
Ilha pessoal (Fase 6) — hub /ilha: progressão linear por tier, construções
com custo em Sachê+sucata e tempo de construção (máquina de estados
idle/building/ready, mesmo padrão do covo do Galdino em cogs/economia.py).

Distinta da "Ilha do Náufrago" (WORLD_LORE["island"] em cogs/economia.py):
aquela é a localidade de lore/exploração do drone (/eco explorar). Esta é a
ilha privada do jogador — conceitos separados por decisão de design.

Cada construção concede um bônus mecânico num eixo próprio (cooldown,
sucata, craft, sorte) e todas escalam por nível — ver ISLAND_BONUS e
island_bonuses. Catálogo (nomes, tema e valores) definido pelo dono: tema
náufrago/sobrevivência pessoal.
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
# indicado em "unlock_tier".
#
# Fase 3: Baú, Bancada e Farol deixaram de ter nível único e passaram a ter
# 5 níveis cada. O motivo é dar motivo CONTÍNUO de gasto — construir uma vez
# e nunca mais voltar fazia a ilha inteira ser um sink de 29.400 Sachês numa
# economia que passa de milhões, e o jogador de meio de jogo (sem rank A e
# sem vara de tier 5) não tem a Forja do Abismo para alimentar. Mesmo
# espírito da Forja, em escala menor e com teto: a ilha é conteúdo de meio de
# jogo, então a escada acaba.
#
# `cost_saches`/`cost_scrap` são o custo do NÍVEL 1; a curva por nível está
# em _structure_cost.
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
        "desc": "Onde você guarda o que a maré traz. Cada nível rende +25% de sucata em toda fonte.",
        "is_core": False,
        "unlock_tier": 1,
        "max_level": 5,
        "cost_saches": 2400,
        "cost_scrap": 240,
        "build_hours": 3,
    },
    "oficina": {
        "name": "Bancada do Náufrago",
        "desc": "Uma bancada improvisada. Barateia o craft em sucata e rende iscas no /eco diario.",
        "is_core": False,
        "unlock_tier": 2,
        "max_level": 5,
        "cost_saches": 4500,
        "cost_scrap": 450,
        "build_hours": 5,
    },
    "farol": {
        "name": "Farol Pessoal",
        "desc": "Um farol erguido à mão na ponta da ilha. Cada nível soma sorte na pescaria.",
        "is_core": False,
        "unlock_tier": 3,
        "max_level": 5,
        "cost_saches": 7500,
        "cost_scrap": 750,
        "build_hours": 8,
    },
}

# --- CURVA DE CUSTO DOS NÍVEIS (só para as não-núcleo) ---
# Sachê dobra a cada nível; sucata cresce LINEAR (base x nível). É a mesma
# assimetria que a Forja do Abismo já usa (FORGE_GROWTH geométrico contra
# FORGE_SCRAP_PER_LEVEL linear), e aqui ela resolve um problema concreto: as
# varas de tier alto têm 0-10% de lixo e quase não produzem sucata, então uma
# curva de sucata geométrica viraria a parede real da ilha em vez do Sachê,
# que é o recurso que a expansão existe para drenar.
#
# Totais das três estruturas, do nível 1 ao 5: 446.400 Sachês e 21.600 de
# sucata (a ilha de nível 1 inteira, incluindo o Acampamento, custa 29.400).
STRUCTURE_COST_GROWTH = 2
# O tempo de obra também cresce por nível, mas com teto: sem ele o nível 5 do
# Farol pediria 40 horas de espera, e uma obra que atravessa dois dias deixa
# de ser progressão para virar castigo.
STRUCTURE_BUILD_HOURS_CAP = 24


# --- BÔNUS MECÂNICOS DAS CONSTRUÇÕES ---
# Cada estrutura mexe num eixo diferente de propósito. Quatro construções
# dando "+X% de renda" seriam a mesma construção quatro vezes, e a decisão de
# qual erguer primeiro deixaria de existir.
#
# Todas as quatro escalam por nível (o Acampamento sempre escalou; as outras
# três passaram a escalar na Fase 3). O nível 1 de cada uma vale exatamente o
# que valia antes — a expansão só acrescenta degraus acima, nunca reduz o que
# quem já construiu tem.
ISLAND_BONUS = {
    # -2% de cooldown de pesca por nível (máx -8% no nível 4)
    "nucleo_cd_por_nivel": 0.02,
    # +25 pontos percentuais de sucata por nível: 1,25x -> 2,25x no nível 5
    "deposito_sucata_por_nivel": 0.25,
    # craft custa metade da sucata no nível 1 e vai caindo 5 pontos por
    # nível, até 30% no nível 5. Nunca chega a zero de propósito: craft de
    # graça apagaria a sucata como recurso.
    "oficina_craft_base": 0.5,
    "oficina_craft_desconto_por_nivel": 0.05,
    # +1 Isca Minhoca por dia por nível (1 no nível 1, 5 no nível 5)
    "oficina_isca_por_nivel": 1,
    # +10% de sorte no nível 1, +5 pontos por nível acima disso (+30% no 5)
    "farol_sorte_base": 0.10,
    "farol_sorte_por_nivel": 0.05,
}


def island_bonuses(structures: dict) -> dict:
    """Bônus ativos a partir do estado das construções.

    Função pura sobre o dicionário de `get_island_structures` para poder ser
    exercitada sem banco. Construção em obra (`status == 'building'`) ainda
    não conta: o nível só sobe em `finalize_island_construction`.
    """
    def nivel(chave):
        estado = structures.get(chave)
        bruto = estado["level"] if estado else 0
        # Teto do catálogo aplicado na leitura, não só na compra: nível
        # gravado acima do máximo (dado velho, correção de catálogo, admin)
        # não pode pagar bônus que a estrutura não oferece.
        return max(0, min(bruto, ISLAND_STRUCTURES[chave]["max_level"]))

    nucleo = nivel("nucleo")
    deposito = nivel("deposito")
    oficina = nivel("oficina")
    farol = nivel("farol")

    return {
        "cd_reducao": nucleo * ISLAND_BONUS["nucleo_cd_por_nivel"],
        "sucata_mult": 1.0 + deposito * ISLAND_BONUS["deposito_sucata_por_nivel"],
        "craft_mult": (
            ISLAND_BONUS["oficina_craft_base"]
            - (oficina - 1) * ISLAND_BONUS["oficina_craft_desconto_por_nivel"]
        ) if oficina else 1.0,
        "sorte_bonus": (
            ISLAND_BONUS["farol_sorte_base"]
            + (farol - 1) * ISLAND_BONUS["farol_sorte_por_nivel"]
        ) if farol else 0.0,
        # Quantidade, não booleano: o consumidor (/eco diario) entrega esse
        # número de iscas. Continua falsy no nível 0, então quem só checava a
        # verdade do campo segue correto.
        "isca_diaria": oficina * ISLAND_BONUS["oficina_isca_por_nivel"],
    }


def get_island_bonuses(conn, user_id: int) -> dict:
    """Versão que lê do banco. Ponto único de consulta para os outros cogs —
    nenhum deles deve reimplementar a leitura das estruturas."""
    return island_bonuses(get_island_structures(conn, user_id))


def _structure_cost(structure_key: str, current_level: int) -> dict:
    """Custo/tempo para levar `structure_key` de current_level para o
    próximo nível.

    Duas curvas diferentes, de propósito:

    * núcleo (Acampamento): custo LINEAR no nível alvo (mesmo padrão de
      try_upgrade_rod). Ele é a progressão, não o sink — encarecê-lo trava o
      acesso às outras construções.
    * demais: Sachê GEOMÉTRICO (dobra por nível) e sucata LINEAR. O Sachê é
      o recurso que a expansão existe para drenar; a sucata só acompanha,
      porque as varas de tier alto quase não produzem lixo e uma curva
      geométrica dos dois lados deixaria a sucata ser a parede de verdade.
    """
    stats = ISLAND_STRUCTURES[structure_key]
    target_level = current_level + 1
    if stats["is_core"]:
        cost_saches = stats["cost_saches_per_level"] * target_level
        cost_scrap = stats["cost_scrap_per_level"] * target_level
        build_hours = stats["build_hours"]
    else:
        cost_saches = stats["cost_saches"] * STRUCTURE_COST_GROWTH ** (target_level - 1)
        cost_scrap = stats["cost_scrap"] * target_level
        build_hours = min(stats["build_hours"] * target_level, STRUCTURE_BUILD_HOURS_CAP)
    return {
        "target_level": target_level,
        "cost_saches": cost_saches,
        "cost_scrap": cost_scrap,
        "build_hours": build_hours,
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
        # "Construído" saiu: desde a Fase 3 toda estrutura tem escada de
        # níveis, então o estado final é sempre "no teto", não "existe".
        return {
            "label": f"✅ Nível Máximo: {stats['name']}",
            "style": discord.ButtonStyle.secondary,
            "disabled": True,
        }

    cost = _structure_cost(structure_key, level)
    verb = "Evoluir" if level > 0 else "Construir"
    return {
        "label": (
            f"{verb}: {stats['name']} Nv{cost['target_level']} "
            f"({cost['cost_saches']}$ / {cost['cost_scrap']}⚙️)"
        ),
        "style": discord.ButtonStyle.primary,
        "disabled": False,
    }


def _bonus_text(structure_key: str, level: int) -> str:
    """Frase curta do que a estrutura entrega NO NÍVEL ATUAL.

    Lê de island_bonuses em vez de reescrever as contas: uma segunda cópia da
    fórmula aqui divergiria da real no primeiro reajuste, e o jogador tomaria
    a decisão de gasto olhando um número que não é o que ele recebe.
    """
    if level <= 0:
        return ""
    b = island_bonuses({structure_key: {"level": level, "status": "idle", "timer_end": None, "state_json": "{}"}})
    if structure_key == "nucleo":
        return f"−{b['cd_reducao'] * 100:.0f}% de cooldown"
    if structure_key == "deposito":
        return f"×{b['sucata_mult']:.2f} de sucata"
    if structure_key == "oficina":
        iscas = b["isca_diaria"]
        return f"craft por {b['craft_mult'] * 100:.0f}% da sucata • +{iscas} isca{'s' if iscas > 1 else ''}/dia"
    if structure_key == "farol":
        return f"+{b['sorte_bonus'] * 100:.0f}% de sorte"
    return ""


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
        linha = f"**{stats['name']}** — {status_text}"
        # O bônus vigente entra na listagem porque a decisão que o hub pede é
        # "vale a pena subir mais um nível desta?" — sem o valor atual ao lado
        # do custo, o jogador não tem como responder sem abrir o código.
        bonus = _bonus_text(key, level)
        if bonus:
            linha += f"\n └ {bonus}"
        lines.append(linha)

    embed.add_field(name="Construções", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Ilha de {user_name} • Cada nível aumenta o bônus da construção")
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
            return await interaction.response.send_message(
                f"✅ **{stats['name']}** já está no nível máximo "
                f"({stats['max_level']}/{stats['max_level']}).",
                ephemeral=True,
            )

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
        # PNG, nao JPG: o nome do anexo acompanha a extensao real do arquivo
        # (o Discord renderiza pelo nome do anexo, nao pelos bytes).
        file, url = get_local_file("assets/locais/ilha_pessoal.png", "ilha_pessoal.png")
        if file:
            embed.set_image(url=url)
            await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(IlhaCog(bot))
