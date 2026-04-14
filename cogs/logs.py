import discord
from discord.ext import commands
from datetime import datetime
import traceback
import sys


class SistemaLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # ID do canal privado de auditoria (servidor pessoal)
        self.canal_auditoria_id = 1489330740876284206

    # ──────────────────────────────────────────────
    # HELPER: enviar embed pro canal de auditoria
    # ──────────────────────────────────────────────

    async def enviar_log(self, embed: discord.Embed):
        canal = self.bot.get_channel(self.canal_auditoria_id)

        # get_channel só acessa o cache local — se o canal não estiver cacheado
        # (comum em servidores onde o bot não está presente ativamente),
        # fazemos uma requisição direta à API do Discord.
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(self.canal_auditoria_id)
            except discord.NotFound:
                print(f"[ERRO] Canal {self.canal_auditoria_id} não existe ou o bot não tem acesso.")
                return
            except discord.Forbidden:
                print(f"[ERRO] Bot sem permissão para acessar o canal {self.canal_auditoria_id}.")
                return
            except Exception as e:
                print(f"[ERRO] Falha ao buscar canal de auditoria: {e}")
                return

        await canal.send(embed=embed)

    # ──────────────────────────────────────────────
    # HELPER: campo de identificação do servidor
    # ──────────────────────────────────────────────

    def campo_servidor(self, guild: discord.Guild | None) -> str:
        """Retorna uma string formatada com nome, ID e ícone do servidor."""
        if guild:
            icone = guild.icon.url if guild.icon else "Sem ícone"
            return f"**{guild.name}**\nID: `{guild.id}`\nÍcone: {icone}"
        return "DM / Servidor desconhecido"

    # ══════════════════════════════════════════════
    # 1. MENSAGEM DELETADA
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not message.guild or message.author.bot:
            return

        embed = discord.Embed(
            title="🗑️ Mensagem Deletada",
            description=(
                f"**Autor:** {message.author.mention} (`{message.author.id}`)\n"
                f"**Canal:** {message.channel.mention}"
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        conteudo = message.content[:1000] if message.content else "[Sem texto / Apenas Mídia]"
        embed.add_field(name="Conteúdo:", value=conteudo, inline=False)
        embed.add_field(name="🌐 Servidor:", value=self.campo_servidor(message.guild), inline=False)
        await self.enviar_log(embed)

    # ══════════════════════════════════════════════
    # 2. MENSAGEM EDITADA
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not before.guild or before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            title="📝 Mensagem Editada",
            description=(
                f"**Autor:** {before.author.mention} (`{before.author.id}`)\n"
                f"**Canal:** {before.channel.mention}\n"
                f"[Ir para a mensagem]({after.jump_url})"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Antes:", value=before.content[:1000] or "[Vazio]", inline=False)
        embed.add_field(name="Depois:", value=after.content[:1000] or "[Vazio]", inline=False)
        embed.add_field(name="🌐 Servidor:", value=self.campo_servidor(before.guild), inline=False)
        await self.enviar_log(embed)

    # ══════════════════════════════════════════════
    # 3. ERRO EM COMANDO (on_command_error)
    #    Captura: comandos inválidos, falta de permissão,
    #    argumento errado, cooldown, conflito, e erros gerais.
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Desempacota o erro original caso venha de um CommandInvokeError
        erro_original = getattr(error, "original", error)

        # ── Categoriza o tipo de erro ──
        if isinstance(error, commands.CommandNotFound):
            return  # Ignora comandos inexistentes (muito spam)

        elif isinstance(error, commands.MissingPermissions):
            titulo = "🔒 Permissão Negada"
            cor = discord.Color.yellow()
            descricao = (
                f"**Usuário sem permissão tentou executar:** `{ctx.command}`\n"
                f"**Permissões faltando:** `{', '.join(error.missing_permissions)}`"
            )

        elif isinstance(error, commands.BotMissingPermissions):
            titulo = "⚠️ Bot Sem Permissão"
            cor = discord.Color.yellow()
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Permissões que o bot não tem:** `{', '.join(error.missing_permissions)}`"
            )

        elif isinstance(error, commands.CommandOnCooldown):
            titulo = "⏳ Cooldown Atingido"
            cor = discord.Color.blurple()
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Tempo restante:** `{error.retry_after:.2f}s`"
            )

        elif isinstance(error, commands.BadArgument):
            titulo = "❌ Argumento Inválido"
            cor = discord.Color.orange()
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Detalhe:** `{error}`"
            )

        elif isinstance(error, commands.MissingRequiredArgument):
            titulo = "❌ Argumento Faltando"
            cor = discord.Color.orange()
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Parâmetro faltando:** `{error.param.name}`"
            )

        elif isinstance(error, commands.CommandInvokeError):
            titulo = "💥 Erro ao Executar Comando"
            cor = discord.Color.red()
            tb = "".join(traceback.format_exception(type(erro_original), erro_original, erro_original.__traceback__))
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Erro:** `{type(erro_original).__name__}: {erro_original}`\n\n"
                f"```py\n{tb[-1800:]}\n```"
            )

        else:
            titulo = "🐛 Erro Desconhecido no Comando"
            cor = discord.Color.dark_red()
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            descricao = (
                f"**Comando:** `{ctx.command}`\n"
                f"**Tipo:** `{type(error).__name__}`\n"
                f"**Detalhe:** `{error}`\n\n"
                f"```py\n{tb[-1800:]}\n```"
            )

        # ── Monta o embed ──
        embed = discord.Embed(title=titulo, description=descricao, color=cor, timestamp=datetime.now())

        # Informações de quem disparou
        if ctx.author:
            embed.add_field(
                name="👤 Executado por:",
                value=f"{ctx.author.mention} (`{ctx.author.id}`)",
                inline=True,
            )
        if ctx.channel:
            embed.add_field(
                name="📍 Canal:",
                value=getattr(ctx.channel, "mention", str(ctx.channel)),
                inline=True,
            )

        embed.add_field(name="🌐 Servidor:", value=self.campo_servidor(ctx.guild), inline=False)
        await self.enviar_log(embed)

    # ══════════════════════════════════════════════
    # 4. CONFLITO / SINCRONIZAÇÃO DE SLASH COMMANDS
    #    Captura erros de comandos de aplicação (/)
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        erro_original = getattr(error, "original", error)
        tb = "".join(traceback.format_exception(type(erro_original), erro_original, erro_original.__traceback__))

        embed = discord.Embed(
            title="⚡ Erro em Slash Command",
            description=(
                f"**Comando:** `{interaction.command.name if interaction.command else 'Desconhecido'}`\n"
                f"**Tipo:** `{type(erro_original).__name__}`\n"
                f"**Detalhe:** `{erro_original}`\n\n"
                f"```py\n{tb[-1800:]}\n```"
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="👤 Executado por:",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=True,
        )
        embed.add_field(
            name="📍 Canal:",
            value=getattr(interaction.channel, "mention", "Desconhecido"),
            inline=True,
        )
        embed.add_field(name="🌐 Servidor:", value=self.campo_servidor(interaction.guild), inline=False)
        await self.enviar_log(embed)

    # ══════════════════════════════════════════════
    # 5. PROBLEMAS DE CONEXÃO
    #    on_disconnect → on_connect/resume
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_disconnect(self):
        embed = discord.Embed(
            title="🔌 Bot Desconectado do Discord",
            description="O bot perdeu a conexão com o gateway do Discord.",
            color=discord.Color.dark_gray(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Aguardando reconexão automática...")
        await self.enviar_log(embed)

    @commands.Cog.listener()
    async def on_connect(self):
        embed = discord.Embed(
            title="✅ Bot Reconectado",
            description="Conexão com o gateway do Discord reestabelecida.",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        await self.enviar_log(embed)

    @commands.Cog.listener()
    async def on_resumed(self):
        embed = discord.Embed(
            title="🔄 Sessão Retomada",
            description="O bot retomou uma sessão existente após queda temporária.",
            color=discord.Color.teal(),
            timestamp=datetime.now(),
        )
        await self.enviar_log(embed)

    # ══════════════════════════════════════════════
    # 6. ERROS GLOBAIS NÃO TRATADOS (crashes)
    #    Captura exceções fora de qualquer listener/cog.
    #    Chame setup_exception_hook(bot) no seu main.py.
    # ══════════════════════════════════════════════

    def registrar_hook_global(self):
        """
        Sobrescreve sys.excepthook para capturar crashes que ocorrem
        fora do loop de eventos do asyncio (ex: erros na inicialização).
        """
        _self = self

        def _hook(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb)
                return
            tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            print(f"[CRASH NÃO TRATADO]\n{tb_str}")
            # Tenta enviar de forma síncrona usando o loop do bot (se disponível)
            loop = _self.bot.loop
            if loop and loop.is_running():
                async def _enviar():
                    embed = discord.Embed(
                        title="💀 CRASH — Erro Não Tratado",
                        description=(
                            f"**Tipo:** `{exc_type.__name__}`\n"
                            f"**Detalhe:** `{exc_value}`\n\n"
                            f"```py\n{tb_str[-1800:]}\n```"
                        ),
                        color=discord.Color.dark_red(),
                        timestamp=datetime.now(),
                    )
                    embed.set_footer(text="Este erro ocorreu FORA do loop de eventos.")
                    await _self.enviar_log(embed)
                loop.create_task(_enviar())

        sys.excepthook = _hook

    # ══════════════════════════════════════════════
    # 7. ERRO ASSÍNCRONO NÃO TRATADO (asyncio)
    #    Captura exceções dentro do loop de eventos
    #    que não foram capturadas por nenhum handler.
    # ══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_error(self, event_method: str, *args, **kwargs):
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_type is None:
            return

        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        embed = discord.Embed(
            title="🔥 Erro Interno no Bot",
            description=(
                f"**Evento:** `{event_method}`\n"
                f"**Tipo:** `{exc_type.__name__}`\n"
                f"**Detalhe:** `{exc_value}`\n\n"
                f"```py\n{tb_str[-1800:]}\n```"
            ),
            color=discord.Color.dark_red(),
            timestamp=datetime.now(),
        )

        # Tenta extrair o guild dos argumentos (nem sempre disponível)
        guild = None
        for arg in args:
            if isinstance(arg, discord.Guild):
                guild = arg
                break
            if hasattr(arg, "guild"):
                guild = arg.guild
                break

        embed.add_field(name="🌐 Servidor:", value=self.campo_servidor(guild), inline=False)
        await self.enviar_log(embed)


# ──────────────────────────────────────────────
# Setup do Cog
# ──────────────────────────────────────────────

async def setup(bot):
    cog = SistemaLogs(bot)
    await bot.add_cog(cog)

    # Registra o hook para crashes fora do loop asyncio
    cog.registrar_hook_global()