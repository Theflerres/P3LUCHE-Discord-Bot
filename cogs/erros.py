iimport discord
from discord.ext import commands
from discord import app_commands
import traceback
import logging
import os
from datetime import datetime
from typing import Optional

from config import LOG_FOLDER, WARN_CHANNEL_ID

# --- CONFIGURAÇÃO DE LOGGING ---
ERRO_LOG_FILE = os.path.join(LOG_FOLDER, "bot_erros.log")

# Cria logger formatado
logger = logging.getLogger("P3LUCHE_ERROS")
logger.setLevel(logging.DEBUG)

# Handler para arquivo
if not logger.handlers:
    file_handler = logging.FileHandler(ERRO_LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # Formato detalhado para arquivo
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s\n%(message)s\n' + '='*80 + '\n',
        datefmt='%d/%m/%Y %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class TratamentoErros(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.error_log_channel: Optional[discord.TextChannel] = None
        bot.tree.on_error = self.on_app_command_error

    async def cog_load(self):
        """Obtém referência do canal de logs de erro ao carregar a cog."""
        try:
            self.error_log_channel = self.bot.get_channel(WARN_CHANNEL_ID)
        except:
            logger.warning("Não foi possível obter o canal de logs de erro")

    def _formatar_contexto_slash(self, interaction: discord.Interaction) -> str:
        """Formata informações de contexto de slash command."""
        return f"""
CONTEXTO DO ERRO:
├─ Tipo: Slash Command (/)
├─ Comando: /{interaction.command.name}
├─ Usuário: {interaction.user} (ID: {interaction.user.id})
├─ Servidor: {interaction.guild.name if interaction.guild else 'DM'} (ID: {interaction.guild_id})
├─ Canal: {interaction.channel.name if interaction.channel else 'N/A'} (ID: {interaction.channel_id})
└─ Timestamp: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
"""

    def _formatar_contexto_prefix(self, ctx: commands.Context) -> str:
        """Formata informações de contexto de comando com prefixo."""
        return f"""
CONTEXTO DO ERRO:
├─ Tipo: Prefix Command ({self.bot.command_prefix})
├─ Comando: {ctx.command}
├─ Argumento: {ctx.message.content}
├─ Usuário: {ctx.author} (ID: {ctx.author.id})
├─ Servidor: {ctx.guild.name if ctx.guild else 'DM'} (ID: {ctx.guild.id if ctx.guild else 'N/A'})
├─ Canal: {ctx.channel.name if ctx.channel else 'N/A'} (ID: {ctx.channel.id})
└─ Timestamp: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
"""

    async def _enviar_para_log_channel(self, titulo: str, erro_info: str, nivel: str = "ERROR"):
        """Envia erro crítico para o canal de logs."""
        if not self.error_log_channel:
            return
        
        try:
            color = {
                "CRITICAL": discord.Color.red(),
                "ERROR": discord.Color.orange(),
                "WARNING": discord.Color.yellow(),
            }.get(nivel, discord.Color.blue())
            
            embed = discord.Embed(
                title=f"🚨 {titulo}",
                description=f"```\n{erro_info[:2000]}\n```",
                color=color,
                timestamp=datetime.now()
            )
            embed.set_footer(text="Sistema de Monitoramento de Erros")
            await self.error_log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Falha ao enviar embed para canal de logs: {e}")

    # --- TRATADOR DE SLASH COMMANDS ( / ) ---
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        contexto = self._formatar_contexto_slash(interaction)
        stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        
        # 1. Erro de Cooldown
        if isinstance(error, app_commands.CommandOnCooldown):
            mensagem = f"⏳ Calma aí, aventureiro! Aguarde **{error.retry_after:.1f}s** para conjurar isso novamente."
            logger.info(f"Cooldown acionado - {interaction.user}: {contexto}")
        
        # 2. Erro de Permissão do usuário
        elif isinstance(error, app_commands.MissingPermissions):
            mensagem = "🚫 Você não tem os privilégios necessários para acessar este pergaminho (Falta de Permissão)."
            logger.warning(f"Acesso negado por permissões - {interaction.user}: {contexto}")
        
        # 3. Erro de Check customizado
        elif isinstance(error, app_commands.CheckFailure):
            mensagem = "🔒 Você não atende aos requisitos para usar este comando no momento."
            logger.warning(f"Check falhou - {interaction.user}: {contexto}")
        
        # 4. Erro de tipo de argumento
        elif isinstance(error, app_commands.TransformerError):
            mensagem = "❌ O tipo ou valor do argumento está inválido. Verifique a sintaxe!"
            logger.error(f"TransformerError: {contexto}\n{stack_trace}")
        
        # 5. Erro de namespace (command group)
        elif isinstance(error, app_commands.CommandSignatureMismatch):
            mensagem = "❌ Erro interno na estrutura do comando."
            logger.error(f"CommandSignatureMismatch: {contexto}\n{stack_trace}")
        
        # 6. Erro geral de app command
        elif isinstance(error, app_commands.AppCommandError):
            mensagem = "❌ Ocorreu um erro ao executar este comando. O incidente foi registrado."
            logger.error(f"AppCommandError: {contexto}\n{stack_trace}")
            await self._enviar_para_log_channel("Erro em Slash Command", f"{contexto}\n{stack_trace}", "ERROR")
        
        # 7. Qualquer outro erro não mapeado
        else:
            mensagem = "❌ Ocorreu um distúrbio crítico na rede. O erro foi registrado para auditoria."
            logger.critical(f"Erro desconhecido em slash command: {contexto}\n{stack_trace}")
            await self._enviar_para_log_channel("Erro Crítico (Desconhecido)", f"{contexto}\n{stack_trace}", "CRITICAL")

        # Envia resposta de forma efêmera
        try:
            if interaction.response.is_done():
                await interaction.followup.send(mensagem, ephemeral=True)
            else:
                await interaction.response.send_message(mensagem, ephemeral=True)
        except Exception as e:
            logger.error(f"Falha ao enviar mensagem de erro: {e}")

    # --- TRATADOR DE COMANDOS COM PREFIXO ( ex: !lore ) ---
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        contexto = self._formatar_contexto_prefix(ctx)
        stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        
        # Ignora comando não encontrado
        if isinstance(error, commands.CommandNotFound):
            logger.debug(f"Comando não encontrado: {ctx.message.content}")
            return
        
        # Erro de argumento obrigatório faltando
        elif isinstance(error, commands.MissingRequiredArgument):
            mensagem = f"⚠️ Faltou o argumento `{error.param.name}` na sua magia! Verifique a sintaxe."
            logger.warning(f"Argumento obrigatório faltando: {contexto}")
        
        # Erro de permissão
        elif isinstance(error, commands.MissingPermissions):
            mensagem = "🚫 Acesso negado pelo sistema de Governança. Você não tem permissão."
            logger.warning(f"Permissão negada: {contexto}")
        
        # Erro de role faltando
        elif isinstance(error, commands.MissingRole):
            mensagem = f"🚫 Você precisa da role `{error.missing_role}` para usar este comando."
            logger.warning(f"Role faltando: {contexto}")
        
        # Erro de argumento inválido
        elif isinstance(error, commands.BadArgument):
            mensagem = "❌ Um dos argumentos fornecidos está inválido ou no formato errado."
            logger.warning(f"Argumento inválido: {contexto}\n{str(error)}")
        
        # Erro de conversão de tipo
        elif isinstance(error, commands.BadUnionArgument):
            mensagem = "❌ Não consegui converter o argumento. Verifique o tipo esperado."
            logger.warning(f"Union argument inválido: {contexto}")
        
        # Erro de NoPrivateMessage
        elif isinstance(error, commands.NoPrivateMessage):
            mensagem = "🚫 Este comando não pode ser usado em DM. Use em um servidor!"
            logger.warning(f"Tentativa de usar comando privado em DM: {contexto}")
        
        # Erro de BotMissingPermissions
        elif isinstance(error, commands.BotMissingPermissions):
            mensagem = f"⚠️ Preciso de permissões para executar isto: {', '.join(error.missing_permissions)}"
            logger.warning(f"Bot sem permissões: {contexto}")
        
        # Erro de cooldown
        elif isinstance(error, commands.CommandOnCooldown):
            mensagem = f"⏳ Espere **{error.retry_after:.1f}s** antes de usar este comando novamente."
            logger.info(f"Cooldown acionado: {contexto}")
        
        # Erro de check customizado
        elif isinstance(error, commands.CheckFailure):
            mensagem = "🔒 Você não atende aos requisitos para usar este comando."
            logger.warning(f"Check falhou: {contexto}")
        
        # Erro geral de comando
        elif isinstance(error, commands.CommandError):
            mensagem = "❌ Erro ao processar o comando. Verifique os argumentos."
            logger.error(f"CommandError: {contexto}\n{stack_trace}")
        
        # Erro de extensão
        elif isinstance(error, commands.ExtensionError):
            mensagem = "❌ Erro no sistema de extensões."
            logger.critical(f"ExtensionError: {contexto}\n{stack_trace}")
            await self._enviar_para_log_channel("Erro em Extensão", f"{contexto}\n{stack_trace}", "CRITICAL")
        
        # Qualquer erro não mapeado
        else:
            mensagem = "❌ Ocorreu um erro desconhecido. O incidente foi registrado."
            logger.critical(f"Erro desconhecido em comando prefix: {contexto}\n{stack_trace}")
            await self._enviar_para_log_channel("Erro Crítico (Prefix)", f"{contexto}\n{stack_trace}", "CRITICAL")

        # Envia resposta
        try:
            await ctx.send(mensagem)
        except Exception as e:
            logger.error(f"Falha ao enviar mensagem de erro em prefix command: {e}")

    # --- GLOBAL ERROR HANDLER (Fallback) ---
    @commands.Cog.listener()
    async def on_error(self, event, *args, **kwargs):
        """Captura erros em event listeners que não são tratados."""
        contexto = f"Event: {event}\nArgs: {args}\nKwargs: {kwargs}"
        stack_trace = traceback.format_exc()
        
        logger.critical(f"Erro não capturado em event listener:\n{contexto}\n{stack_trace}")
        await self._enviar_para_log_channel("Erro Crítico em Event Listener", f"{contexto}\n{stack_trace}", "CRITICAL")


async def setup(bot):
    cog = TratamentoErros(bot)
    await bot.add_cog(cog)
    await cog.cog_load()