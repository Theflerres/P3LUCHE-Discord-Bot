"""
Comandos gerais — ajuda, stats, apoiadores e grupo /ia.

mensagem_manual, catches_inspect, catches_reset, admin_quest e admin_fix_time
migraram para /admin sistema|debug (cogs/admin.py) na Fase 7 — de lá pra cá
ganharam checagem de permissão via is_bot_owner() (admin_quest não tinha
NENHUMA checagem antes; admin_fix_time usava uma lista de IDs hardcoded).
"""
import asyncio
import json
from datetime import datetime

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from config import MOD_ROLE_IDS, set_bot_instance
from economy_db import get_inventory
from cogs.economia import WEATHER_EFFECTS
from cogs.ilha import ISLAND_HUB_LORE
from cogs.onboarding import ADD_APP_STEPS


class HelpSelect(discord.ui.Select):
    def __init__(self, bot_ref, user):
        self.bot = bot_ref
        self.user_ref = user

        options = [
            discord.SelectOption(
                label="Painel Administrativo",
                description="Comandos de Moderação e Governança.",
                emoji="🔐",
                value="staff",
            ),
            discord.SelectOption(
                label="Manual do Usuário",
                description="Comandos de Diversão, Pesca e Música.",
                emoji="🎮",
                value="member",
            ),
        ]
        super().__init__(placeholder="Alterne a visualização aqui...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_ref.id:
            return await interaction.response.send_message("Esse menu não é pra você, curioso.", ephemeral=True)

        if self.values[0] == "staff":
            embed = create_staff_embed(self.bot, self.user_ref)
        else:
            embed = create_member_embed(self.bot)

        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, bot_ref, user):
        super().__init__(timeout=180)
        self.add_item(HelpSelect(bot_ref, user))


def create_member_embed(bot_ref):
    avatar_url = bot_ref.user.avatar.url if bot_ref.user.avatar else None

    # Status do cassino lido em tempo real (o cog só existe registrado no
    # bot se cogs.casino estiver na lista de extensões carregadas em
    # main.py) — evita a dica ficar desatualizada se isso mudar no futuro.
    casino_ativo = bot_ref.get_cog("CasinoCog") is not None
    casino_status = "" if casino_ativo else " (**desativado por enquanto**, corrigindo bugs)"
    clima_nomes = ", ".join(w["name"] for w in WEATHER_EFFECTS.values())

    overview = (
        "Olá! Eu sou o **P3LUCHE** — aqui está o que dá pra fazer neste servidor:\n\n"
        f"🎣 **Economia & Pesca** — pesque Sachês e itens com `/eco pescar`, evolua sua vara "
        f"e sucata, e fique de olho no clima ({clima_nomes}), que muda o que você pesca.\n"
        f"{ISLAND_HUB_LORE['title']} — construa e evolua sua própria ilha com `/ilha`, "
        "em tiers, no seu próprio ritmo (sem visita de outros jogadores).\n"
        f"🎰 **Cassino**{casino_status} — minijogos que apostam peixes e Sachês.\n"
        "🎵 **Música** — toca e organiza um catálogo de músicas salvas com `/musica`.\n"
        "🛡️ **Moderação** — advertências com trilha de auditoria (cargo de staff).\n"
        "📜 **Lore & IA** — registre a história do seu personagem, veja o grafo de conexões "
        "entre players e converse com a IA sobre a lore do servidor.\n\n"
        "Comandos por categoria abaixo 👇"
    )

    embed = discord.Embed(
        title="📘 Manual do Usuário - P3LUCHE v3.0",
        description=overview,
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=avatar_url)

    embed.add_field(
        name="⚠️ Comandos não aparecem ou não respondem?",
        value=(
            "Você provavelmente precisa autorizar o P3LUCHE pra sua própria conta, mesmo "
            "já estando no servidor:\n\n" + ADD_APP_STEPS
        ),
        inline=False,
    )

    eco_txt = (
        "`/eco pescar` - Tenta pegar peixes (ou lixo) para ganhar Sachês.\n"
        "`/eco loja` - Vê os itens à venda hoje (Rotação Diária).\n"
        "`/eco comprar [item]` - Gasta seus Sachês. Cuidado com o vício!\n"
        "`/eco saldo` - Mostra sua carteira, iscas e itens raros.\n"
        "`/eco rank` - Quem são os magnatas do servidor?"
    )
    embed.add_field(name="🎣 Economia & Pescaria", value=eco_txt, inline=False)

    music_txt = (
        "`/musica adicionar [link]` - Guarda músicas do YouTube no acervo.\n"
        "`/musica biblioteca` - Vê o catálogo de músicas salvas.\n"
        "`/musica buscar [termo]` - Pesquisa músicas pelo nome."
    )
    embed.add_field(name="🎵 Rádio", value=music_txt, inline=False)

    lore_txt = (
        "`/lore player` - Registra a história do SEU personagem.\n"
        "`/lore ler [id]` - Lê uma história completa com páginas.\n"
        "`/lore grafo` - Gera a teia visual de conexões entre players.\n"
        "`/p3luche enquete` - Cria uma enquete rápida com reações.\n"
        "`/apoiadores` - Veja quem mantém meus servidores ligados!"
    )
    embed.add_field(name="📜 Roleplay & Comunidade", value=lore_txt, inline=False)

    # Registrar memórias por menção (@P3LUCHE "lembre-se que...") é restrito a
    # Staff/Criador (checagem em lore_ai.py:on_message) — não prometer isso
    # aqui para membros comuns, que nunca conseguiriam usar.
    ia_txt = "`/ia memoria_ver` - Veja o que eu sei sobre você (se algo já foi anotado)."
    embed.add_field(name="🤖 Inteligência Artificial", value=ia_txt, inline=False)

    embed.set_footer(text="Dica: Use /eco diario todo dia para ganhar Sachês grátis!")
    return embed


def create_staff_embed(bot_ref, user):
    avatar_url = bot_ref.user.avatar.url if bot_ref.user.avatar else None
    embed = discord.Embed(
        title="🔐 Painel de Administração - P3LUCHE OS",
        description="**Nível de Acesso: SUPERVISOR.**\nAqui estão os protocolos avançados.",
        color=discord.Color.dark_red(),
    )
    embed.set_thumbnail(url=avatar_url)

    mod_txt = (
        "`/mod advertencia [user] [motivo] [provas]`\n"
        "> Gera Nota Fiscal e avisa na DM. 4 Warns = Sugestão de Ban.\n"
        "`/mod historico [user]` - Vê ficha criminal completa.\n"
        "`/mod perdoar [id]` - Revoga um warn (Soft Delete)."
    )
    embed.add_field(name="⚖️ Sistema de Justiça", value=mod_txt, inline=False)

    lore_admin = (
        "`/acervo` - Abre o HUB para ver/editar lore de QUALQUER player.\n"
        "`/lore server` - Adiciona Lore Global do Mundo.\n"
        "`/lore editar [tipo] [user]` - Edita textos de terceiros.\n"
        "`/lore diff [id_versao]` - Vê o que mudou entre edições."
    )
    embed.add_field(name="🏛️ Bibliotecário-Chefe (Lore)", value=lore_admin, inline=False)

    music_admin = (
        "`/musica editar [id] [novo_nome]` - Renomeia faixas erradas.\n"
        "`/musica ocultar [id]` - Remove música da biblioteca (Lixeira).\n"
        "`/musica restaurar [id]` - Traz música de volta."
    )
    embed.add_field(name="🎧 Gestão de Mídia", value=music_admin, inline=False)

    extra_txt = (
        "`/admin sistema falar [canal] [msg]` - O bot fala por você.\n"
        "`/stats` - Monitoramento de CPU/RAM e Latência.\n"
        "`/p3luche comandos` - Tradução, resumo e reescrita de texto via IA "
        "(restrito a Staff/Criador, assim como conversar por menção)."
    )
    embed.add_field(name="⚙️ Ferramentas do Sistema", value=extra_txt, inline=False)

    embed.set_footer(text=f"Olá, Chefe {user.name}. Use o menu abaixo para ver os comandos de membro.")
    return embed


# --- PAINEL DE APOIADORES (hub com botões, no molde de CityHubView/IlhaHubView) ---
# Cargos atribuídos manualmente pela staff após a doação — a detecção
# continua sendo "tem o cargo ou não tem", só a apresentação mudou.
SUPPORTER_ROLE_IDS = {
    "vip": 1444466894445740253,
    "veterano": 1313612976833429504,
    "tita": 1453158945664270386,
}
# VIP e Veterano são mutuamente exclusivos pra fins de "tela principal" —
# VIP tem prioridade, como pedido. Escudo da Tita NÃO entra nessa exclusão:
# quem tem o cargo do Escudo sempre aparece na tela do Escudo, além de onde
# mais aparecer. O selo 🛡️ na tela principal é um extra, nunca substitui a
# presença na tela do Escudo (bug corrigido: a primeira versão tratava as
# três categorias como mutuamente exclusivas entre si, o que esvaziava a
# tela do Escudo pra qualquer pessoa que também fosse VIP ou Veterano).
SUPPORTER_BADGE_EMOJI = {"vip": "💎", "veterano": "🏛️", "tita": "🛡️"}
# Ordem de exibição na legenda — só veterano/tita aparecem como selo em
# alguma tela (vip nunca é selo, sempre é tela principal quando presente).
SUPPORTER_BADGE_ORDER = ("veterano", "tita")
SUPPORTER_BADGE_LEGEND = {
    "veterano": "🏛️ também é Apoiador Veterano",
    "tita": "🛡️ também apoiou o Escudo da Tita",
}


def _member_role_ids(member) -> set:
    return {r.id for r in member.roles}


def categorize_supporters(members) -> dict:
    """Recebe um iterável de membros (qualquer objeto com `.roles`, cada
    role com `.id` — discord.Member de verdade em produção, ou um dublê
    simples nos testes) e devolve {"vip": [...], "veterano": [...], "tita": [...]}.

    VIP e Veterano são mutuamente exclusivos (VIP tem prioridade como tela
    principal); o Escudo da Tita é aditivo — quem tem esse cargo SEMPRE
    aparece na lista "tita", independente de também aparecer em vip/veterano
    com um selo 🛡️ lá.
    """
    result = {"vip": [], "veterano": [], "tita": []}
    for member in members:
        role_ids = _member_role_ids(member)
        has_vip = SUPPORTER_ROLE_IDS["vip"] in role_ids
        has_veterano = SUPPORTER_ROLE_IDS["veterano"] in role_ids
        has_tita = SUPPORTER_ROLE_IDS["tita"] in role_ids

        primary = "vip" if has_vip else ("veterano" if has_veterano else None)
        if primary is not None:
            badges = []
            if primary == "vip" and has_veterano:
                badges.append(SUPPORTER_BADGE_EMOJI["veterano"])
            if has_tita:
                badges.append(SUPPORTER_BADGE_EMOJI["tita"])
            result[primary].append((member, badges))

        if has_tita:
            result["tita"].append((member, []))

    return result


async def resolve_supporter_display(guild, bot, member) -> str:
    """Confirma se `member` ainda está no servidor. Primeiro tenta pelo
    cache local (`guild.get_member`, sem custo de rede — cobre a
    esmagadora maioria dos apoiadores, que estão mesmo no servidor) e só
    recorre a chamadas de rede (`fetch_member`, depois `fetch_user`) para
    quem o cache não resolve mais — o caso raro que motivou a Opção B.

    `get_member` é checado de novo aqui (não reaproveita o objeto `member`
    capturado na varredura de guild.members) porque o cache pode ter sido
    corrigido entre a categorização e a resolução (ex: o bot recebeu o
    evento de saída bem nessa janela) — checar de novo, ao vivo mas ainda
    local/gratuito, aproveita essa correção sem custo de rede.

    Se a pessoa realmente saiu, ainda tenta mostrar o nome dela (busca
    global de conta, funciona mesmo sem servidor em comum) marcado como
    "saiu do servidor", em vez de simplesmente sumir com o registro —
    apoiou, então o nome fica. Se nem a conta existir mais, cai num texto
    genérico: nunca deixa um ID cru sem nome aparecer no painel.
    """
    if guild.get_member(member.id) is not None:
        return member.mention

    try:
        await guild.fetch_member(member.id)
        return member.mention
    except discord.HTTPException:
        pass

    try:
        user = await bot.fetch_user(member.id)
        return f"{user.display_name} *(saiu do servidor)*"
    except discord.HTTPException:
        return "Apoiador (conta removida)"


async def resolve_categorized_supporters(guild, bot, categorized: dict) -> dict:
    """Troca cada (member, badges) por (texto_resolvido, badges) nas três
    categorias — resolvido uma única vez por invocação do comando; navegar
    entre as telas do hub reaproveita o resultado, sem checar de novo.

    Resolve todo mundo em paralelo via asyncio.gather em vez de um por um
    — o tempo total passa a ser o da chamada mais lenta, não a soma de
    todas (relevante mesmo com o cache-first acima, já que quem cai no
    caminho de rede ainda paga o custo daquela chamada específica)."""
    flat = [
        (category, member, badges)
        for category, entries in categorized.items()
        for member, badges in entries
    ]
    displays = await asyncio.gather(*(resolve_supporter_display(guild, bot, member) for _, member, _ in flat))

    resolved = {"vip": [], "veterano": [], "tita": []}
    for (category, _, badges), display in zip(flat, displays):
        resolved[category].append((display, badges))
    return resolved


def _format_supporter_list(entries: list) -> str:
    """`entries` é (display, badges) — `display` já vem resolvido (mention
    normal, nome com marca de "saiu do servidor", ou o texto genérico de
    conta removida). Esta função só formata, não decide mais nada."""
    if not entries:
        return "Ninguém... por enquanto. 😿"
    lines = []
    for display, badges in entries:
        suffix = f" {''.join(badges)}" if badges else ""
        lines.append(f"{display}{suffix}")
    return "\n".join(lines)


def _supporter_legend(entries: list) -> str | None:
    """Legenda só aparece se algum selo realmente estiver em uso nesta tela."""
    used = {badge_cat for _, badges in entries for badge_cat, emoji in SUPPORTER_BADGE_EMOJI.items() if emoji in badges}
    if not used:
        return None
    return "\n".join(SUPPORTER_BADGE_LEGEND[cat] for cat in SUPPORTER_BADGE_ORDER if cat in used)


def build_vip_embed(categorized: dict) -> discord.Embed:
    entries = categorized["vip"]
    embed = discord.Embed(
        title="💎 Apoiadores Ativos",
        description=(
            f"**{len(entries)} apoiador(es)** mantendo o P3LUCHE vivo agora.\n\n"
            + _format_supporter_list(entries)
        ),
        color=discord.Color.from_rgb(255, 105, 180),
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2904/2904973.png")
    legend = _supporter_legend(entries)
    if legend:
        embed.add_field(name="Legenda", value=legend, inline=False)
    embed.set_footer(text="Quer ajudar e ganhar destaque? Fale com a Staff!")
    return embed


def build_veteranos_embed(categorized: dict) -> discord.Embed:
    entries = categorized["veterano"]
    embed = discord.Embed(
        title="🏛️ Apoiadores Veteranos",
        description="Doadores da temporada anterior do FCN.\n\n" + _format_supporter_list(entries),
        color=discord.Color.gold(),
    )
    legend = _supporter_legend(entries)
    if legend:
        embed.add_field(name="Legenda", value=legend, inline=False)
    return embed


def build_tita_embed(categorized: dict) -> discord.Embed:
    entries = categorized["tita"]
    embed = discord.Embed(
        title="🛡️ Escudo da Tita",
        description=(
            f"**{len(entries)} pessoa(s)** ajudaram quando foi preciso.\n\n"
            "Uma campanha especial, por tempo limitado, criada para ajudar uma das "
            "artistas do servidor quando a Tita precisou. A campanha já se encerrou, "
            "e as recompensas ficaram só naquele período — cada nome aqui é de quem "
            "topou ajudar na hora que foi preciso.\n\n" + _format_supporter_list(entries)
        ),
        color=discord.Color.purple(),
    )
    # Quem tem o cargo do Escudo sempre cai aqui, mesmo sendo também VIP/
    # Veterano (ver categorize_supporters) — mas nunca carrega selo NESTA
    # tela, então sem legenda; o selo 🛡️ só aparece na tela principal dela.
    return embed


class ApoiadoresBackView(discord.ui.View):
    """Botão único de volta, usado pelas subtelas (Veteranos/Tita)."""

    def __init__(self, categorized: dict):
        super().__init__(timeout=180)
        self.categorized = categorized

    @discord.ui.button(label="⬅️ Voltar", style=discord.ButtonStyle.secondary)
    async def voltar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_vip_embed(self.categorized), view=ApoiadoresView(self.categorized))


class ApoiadoresView(discord.ui.View):
    """Hub de /apoiadores — tela inicial é sempre o VIP; os outros dois
    botões abrem subtelas dedicadas, cada uma com um botão de volta. Painel
    é público (mensagem não-ephemeral, como já era antes) — não há dado
    privado de ninguém aqui, então a navegação não é travada a quem
    disparou o comando, ao contrário dos hubs pessoais (CityHubView é por
    localização, não por dono; IlhaHubView SIM trava por dono porque mexe
    com saldo/construções de um jogador específico — este painel não mexe
    com nada, só exibe)."""

    def __init__(self, categorized: dict):
        super().__init__(timeout=180)
        self.categorized = categorized

    @discord.ui.button(label="🏛️ Veteranos", style=discord.ButtonStyle.secondary)
    async def veteranos_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_veteranos_embed(self.categorized), view=ApoiadoresBackView(self.categorized)
        )

    @discord.ui.button(label="🛡️ Escudo da Tita", style=discord.ButtonStyle.secondary)
    async def tita_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_tita_embed(self.categorized), view=ApoiadoresBackView(self.categorized)
        )


ia_group = app_commands.Group(name="ia", description="Configurações da mente do P3LUCHE.")


class SistemaCog(commands.Cog):
    """Stats, ajuda, apoiadores, catches, /ia, admins e garrafa."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        self.bot.tree.add_command(ia_group)

    @app_commands.command(name="stats", description="Mostra estatísticas detalhadas do sistema.")
    async def stats(self, interaction: discord.Interaction):
        ping = round(self.bot.latency * 1000)

        uptime_str = "Calculando..."
        if hasattr(self.bot, "start_time"):
            uptime = datetime.now() - self.bot.start_time
            uptime_str = str(uptime).split(".")[0]

        cpu_usage = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ram_used = round(ram.used / 1024**3, 2)
        ram_total = round(ram.total / 1024**3, 2)
        ram_percent = ram.percent

        server_count = len(self.bot.guilds)
        member_count = sum(guild.member_count for guild in self.bot.guilds)

        embed = discord.Embed(
            title="📊 Estatísticas do Sistema P3LUCHE",
            color=discord.Color.purple(),
            timestamp=datetime.now(),
        )

        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)

        embed.add_field(
            name="⚡ Performance",
            value=f"**Ping:** `{ping}ms`\n**Uptime:** `{uptime_str}`",
            inline=True,
        )
        embed.add_field(
            name="🖥️ Hardware",
            value=f"**CPU:** `{cpu_usage}%`\n**RAM:** `{ram_used}/{ram_total}GB` ({ram_percent}%)",
            inline=True,
        )
        embed.add_field(
            name="🌐 Alcance",
            value=f"**Servidores:** `{server_count}`\n**Usuários:** `{member_count}`",
            inline=False,
        )

        embed.set_footer(
            text=f"Solicitado por {interaction.user.name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="apoiadores", description="Homenageia os nobres financiadores do P3LUCHE.")
    async def apoiadores(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ Use no servidor.", ephemeral=True)

        # Defer: resolve_categorized_supporters faz uma checagem ao vivo por
        # apoiador (fetch_member, e fetch_user pra quem já saiu) — pode
        # passar da janela de 3s de uma interação não-deferida.
        await interaction.response.defer()

        categorized = categorize_supporters(guild.members)
        resolved = await resolve_categorized_supporters(guild, self.bot, categorized)
        await interaction.followup.send(embed=build_vip_embed(resolved), view=ApoiadoresView(resolved))

    @app_commands.command(name="ajuda", description="Mostra o manual de comandos.")
    async def ajuda(self, interaction: discord.Interaction):
        is_staff = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)

        if is_staff:
            embed = create_staff_embed(self.bot, interaction.user)
            view = HelpView(self.bot, interaction.user)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            embed = create_member_embed(self.bot)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ler_garrafa", description="Abre a Garrafa Incrustada para ler a mensagem dentro.")
    async def ler_garrafa(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cursor = self.bot.db_conn.cursor()

        has_bottle = False
        q_row = cursor.execute("SELECT inventory FROM quest_progress WHERE user_id = ?", (user_id,)).fetchone()
        if q_row and q_row["inventory"]:
            try:
                qinv = q_row["inventory"]
                if isinstance(qinv, str):
                    qinv = json.loads(qinv)
                if isinstance(qinv, dict) and "garrafa_incrustada" in qinv:
                    has_bottle = True
            except Exception:
                pass

        if not has_bottle:
            # Mochila da v4 (user_inventory) em vez do JSON de economy.inventory:
            # a garrafa pode ter vindo da pesca ou do /debug quest, que gravam
            # pela v4. get_inventory() já filtra quantidade > 0, então um
            # resquício com quantidade 0 deixa de contar como "tem a garrafa".
            if get_inventory(self.bot.db_conn, user_id).get("garrafa_incrustada"):
                has_bottle = True

        if not has_bottle:
            return await interaction.response.send_message(
                "❌ Você não tem nenhuma **Garrafa Incrustada**.",
                ephemeral=True,
            )

        await interaction.response.defer()

        carta_texto = (
            "📅 *Data: Desconhecida // Autor: Capitão Correnteza*\n\n"
            "\"Se você está lendo isso, o *Lamento do Mar* já não existe.\n"
            "O nível da água... você notou? Não é a maré. O mar está descendo.\n"
            "A **Fenda** não é apenas um buraco. É uma boca.\n"
            "**Ele acordou.** O Pai Primordial está faminto.\n\n"
            "Leve este selo para a **Capitã Jenna** em Porto Solare. Diga que a Guarda Real caiu.\""
        )

        embed = discord.Embed(
            title="📜 Carta do Capitão Correnteza",
            description=carta_texto,
            color=discord.Color.dark_gold(),
        )
        embed.set_footer(text="Item Recebido: Selo do Capitão")

        cursor.execute(
            """
            INSERT INTO quest_progress (user_id, inventory, current_chapter) VALUES (?, '{"selo_capitao": 1}', 'acesso_liberado')
            ON CONFLICT(user_id) DO UPDATE SET inventory = '{"selo_capitao": 1}', current_chapter = 'acesso_liberado'
        """,
            (user_id,),
        )
        self.bot.db_conn.commit()

        await interaction.followup.send(embed=embed)
        await interaction.followup.send(
            f"🔑 **Item Chave Adquirido:** [Selo do Capitão].\nAgora você tem permissão para entrar em **Porto Solare**!",
            ephemeral=True,
        )


@ia_group.command(name="memoria_ver", description="Mostra tudo o que eu lembro sobre você.")
async def ia_memoria_ver(interaction: discord.Interaction):
    cursor = interaction.client.db_conn.cursor()
    rows = cursor.execute(
        """
        SELECT id, memory_text, created_at
        FROM user_memories
        WHERE user_id = ? AND is_active = 1
        ORDER BY created_at DESC
    """,
        (interaction.user.id,),
    ).fetchall()

    if not rows:
        return await interaction.response.send_message(
            "🧠 Minha mente está vazia em relação a você. (Nenhuma memória salva)",
            ephemeral=True,
        )

    embed = discord.Embed(title=f"🧠 Memórias de {interaction.user.name}", color=discord.Color.magenta())
    embed.set_footer(text="Use /ia memoria_esquecer [ID] para apagar algo.")

    desc = ""
    for row in rows:
        desc += f"🆔 **{row['id']}** | 📅 {row['created_at']}\n📝 *{row['memory_text']}*\n\n"

    if len(desc) > 4000:
        desc = desc[:4000] + "... (lista cortada)"
    embed.description = desc

    await interaction.response.send_message(embed=embed, ephemeral=True)


@ia_group.command(name="memoria_esquecer", description="Apaga uma memória específica pelo ID.")
@app_commands.describe(id_memoria="O ID da memória para apagar")
async def ia_memoria_esquecer(interaction: discord.Interaction, id_memoria: int):
    cursor = interaction.client.db_conn.cursor()

    mem = cursor.execute("SELECT user_id, is_active FROM user_memories WHERE id = ?", (id_memoria,)).fetchone()

    if not mem:
        return await interaction.response.send_message("❌ Memória não encontrada.", ephemeral=True)

    if mem["user_id"] != interaction.user.id:
        return await interaction.response.send_message("🚫 Você não pode apagar memórias de outras pessoas!", ephemeral=True)

    if mem["is_active"] == 0:
        return await interaction.response.send_message("⚠️ Essa memória já foi apagada.", ephemeral=True)

    cursor.execute("UPDATE user_memories SET is_active = 0 WHERE id = ?", (id_memoria,))
    interaction.client.db_conn.commit()

    await interaction.response.send_message(
        f"🗑️ Memória **{id_memoria}** removida dos meus circuitos.",
        ephemeral=True,
    )


async def setup(bot):
    await bot.add_cog(SistemaCog(bot))
