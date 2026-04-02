"""
Comandos gerais — ajuda, stats, apoiadores, mensagens manuais, debug e grupo /ia.
"""
import json
from datetime import datetime

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from config import (
    CATCHES_LOCK,
    CATCHES_SINCE_RESTART,
    CREATOR_ID,
    MOD_ROLE_IDS,
    set_bot_instance,
)


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
    embed = discord.Embed(
        title="📘 Manual do Usuário - P3LUCHE v3.0",
        description="Olá! Aqui está tudo o que você pode fazer para interagir comigo.",
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=avatar_url)

    eco_txt = (
        "`/eco pescar` - Tenta pegar peixes (ou lixo) para ganhar Sachês.\n"
        "`/eco loja` - Vê os itens à venda hoje (Rotação Diária).\n"
        "`/eco comprar [item]` - Gasta seus Sachês. Cuidado com o vício!\n"
        "`/eco saldo` - Mostra sua carteira, iscas e itens raros.\n"
        "`/eco rank` - Quem são os magnatas do servidor?"
    )
    embed.add_field(name="🎣 Economia & Pescaria", value=eco_txt, inline=False)

    music_txt = (
        "`/musica adicionar [link]` - Toca músicas do YouTube.\n"
        "`/biblioteca` - Vê o catálogo de músicas salvas.\n"
        "`/musica buscar [termo]` - Pesquisa músicas pelo nome."
    )
    embed.add_field(name="🎵 Rádio", value=music_txt, inline=False)

    lore_txt = (
        "`/lore player` - Registra a história do SEU personagem.\n"
        "`/lore ler [id]` - Lê uma história completa com páginas.\n"
        "`/lore grafo` - Gera a teia visual de conexões entre players.\n"
        "`/apoiadores` - Veja quem mantém meus servidores ligados!"
    )
    embed.add_field(name="📜 Roleplay & Comunidade", value=lore_txt, inline=False)

    ia_txt = (
        "**Conversa:** Me mencione (@P3LUCHE) para bater papo.\n"
        "**Memória:** Diga 'Lembre-se que [algo]' para eu anotar.\n"
        "`/ia memoria_ver` - Veja o que eu sei sobre você."
    )
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
        "`/advertencia [user] [motivo] [provas]`\n"
        "> Gera Nota Fiscal e avisa na DM. 4 Warns = Sugestão de Ban.\n"
        "`/historico [user]` - Vê ficha criminal completa.\n"
        "`/perdoar [id]` - Revoga um warn (Soft Delete)."
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
        "`/mensagem_manual [canal] [msg]` - O bot fala por você.\n"
        "`/stats` - Monitoramento de CPU/RAM e Latência."
    )
    embed.add_field(name="⚙️ Ferramentas do Sistema", value=extra_txt, inline=False)

    embed.set_footer(text=f"Olá, Chefe {user.name}. Use o menu abaixo para ver os comandos de membro.")
    return embed


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
        ID_RECENTE = 1444466894445740253
        ID_ANTIGO = 1313612976833429504
        ID_TITA = 1453158945664270386

        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ Use no servidor.", ephemeral=True)

        recentes = []
        antigos = []
        herois_tita = []

        for member in guild.members:
            roles_id = [r.id for r in member.roles]
            if ID_TITA in roles_id:
                herois_tita.append(f"🛡️ {member.mention}")
            if ID_RECENTE in roles_id:
                recentes.append(f"💎 {member.mention}")
            if ID_ANTIGO in roles_id:
                antigos.append(f"🏛️ {member.mention}")

        def format_list(lista):
            return "\n".join(lista) if lista else "Ninguém... por enquanto. 😿"

        embed = discord.Embed(
            title="💖 Hall da Fama: Financiadores",
            description="Graças a estes humanos incríveis, o P3LUCHE continua vivo e operante!",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2904/2904973.png")

        if herois_tita:
            embed.add_field(
                name="🛡️ Escudo da Tita (Campanha Especial)",
                value=f"{format_list(herois_tita)}\n*Ajudaram na saúde da nossa gatinha.*",
                inline=False,
            )

        embed.add_field(name="💎 Apoiadores Ativos (VIP)", value=format_list(recentes), inline=False)
        embed.add_field(name="🏛️ Apoiadores Veteranos", value=format_list(antigos), inline=False)

        embed.set_footer(text="Quer ajudar e ganhar destaque? Fale com a Staff!")

        await interaction.response.send_message(embed=embed)

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

    @app_commands.command(name="mensagem_manual", description="Envia uma mensagem manual em um canal específico (Apenas Criador).")
    @app_commands.describe(
        canal="Selecione o canal onde a mensagem será enviada.",
        mensagem="O conteúdo da mensagem a ser enviada.",
    )
    async def mensagem_manual(self, interaction: discord.Interaction, canal: discord.TextChannel, mensagem: str):
        if interaction.user.id != CREATOR_ID:
            await interaction.response.send_message(
                "🚫 Acesso Negado. Apenas o meu criador pode usar este comando.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await canal.send(mensagem)
            await interaction.followup.send(
                f"✅ Mensagem enviada com sucesso para o canal {canal.mention}!",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Erro: Não tenho permissão para falar no canal {canal.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao enviar: {e}", ephemeral=True)

    @app_commands.command(name="catches_inspect", description="Inspeciona contador de pescas desde o último restart (Apenas Criador).")
    async def catches_inspect(self, interaction: discord.Interaction):
        if interaction.user.id != CREATOR_ID:
            return await interaction.response.send_message("🚫 Apenas o criador pode usar.", ephemeral=True)
        with CATCHES_LOCK:
            items = sorted(CATCHES_SINCE_RESTART.items(), key=lambda x: x[1], reverse=True)
        if not items:
            return await interaction.response.send_message(
                "Nenhuma pesca registrada desde o restart.",
                ephemeral=True,
            )
        text = "\n".join([f"<@{uid}>: {cnt}" for uid, cnt in items[:50]])
        await interaction.response.send_message(f"Contador de pescas (top 50):\n{text}", ephemeral=True)

    @app_commands.command(name="catches_reset", description="Reseta contador de pescas desde o restart (Apenas Criador).")
    @app_commands.describe(user="ID do usuário (opcional) para resetar somente esse usuário")
    async def catches_reset(self, interaction: discord.Interaction, user: discord.User = None):
        if interaction.user.id != CREATOR_ID:
            return await interaction.response.send_message("🚫 Apenas o criador pode usar.", ephemeral=True)
        with CATCHES_LOCK:
            if user:
                removed = CATCHES_SINCE_RESTART.pop(user.id, None)
                await interaction.response.send_message(
                    f"Resetado contador de <@{user.id}> (antes: {removed}).",
                    ephemeral=True,
                )
            else:
                CATCHES_SINCE_RESTART.clear()
                await interaction.response.send_message(
                    "Resetado contador global de pescas desde restart.",
                    ephemeral=True,
                )

    @app_commands.command(name="admin_quest", description="[DEBUG] Força o ganho da Garrafa para testes.")
    async def admin_quest(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cursor = self.bot.db_conn.cursor()

        row = cursor.execute("SELECT inventory FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return await interaction.response.send_message("Crie uma conta pescando primeiro.", ephemeral=True)

        try:
            inv = json.loads(row["inventory"])
        except Exception:
            inv = {}

        inv["garrafa_incrustada"] = 1

        cursor.execute("UPDATE quest_progress SET current_chapter = 'inicio' WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE economy SET inventory = ? WHERE user_id = ?", (json.dumps(inv), user_id))
        self.bot.db_conn.commit()

        await interaction.response.send_message(
            "🛠️ **DEBUG:** Garrafa adicionada e Quest resetada.\nTeste agora usando `/ler_garrafa`.",
            ephemeral=True,
        )

    @app_commands.command(name="admin_fix_time", description="Reseta cooldowns bugados.")
    async def admin_fix_time(self, interaction: discord.Interaction):
        if interaction.user.id not in [299323165937500160, 541680099477422110]:
            return await interaction.response.send_message("🚫 Admin only.", ephemeral=True)
        cursor = self.bot.db_conn.cursor()
        cursor.execute("UPDATE economy SET last_fish = NULL, last_explore = NULL")
        self.bot.db_conn.commit()
        await interaction.response.send_message("✅ Tempo resetado!", ephemeral=True)

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
            e_row = cursor.execute("SELECT inventory FROM economy WHERE user_id = ?", (user_id,)).fetchone()
            if e_row and e_row["inventory"]:
                try:
                    einv = json.loads(e_row["inventory"]) if isinstance(e_row["inventory"], str) else e_row["inventory"]
                    if einv and "garrafa_incrustada" in einv:
                        has_bottle = True
                except Exception:
                    pass

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
