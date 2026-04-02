import discord
from discord.ext import commands
from discord import app_commands

class TratamentoErros(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Sobrescreve o tratador de erros padrão da "árvore" de Slash Commands
        bot.tree.on_error = self.on_app_command_error

    # --- TRATADOR DE SLASH COMMANDS ( / ) ---
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        
        # 1. Erro de Cooldown (Tempo de recarga do comando)
        if isinstance(error, app_commands.CommandOnCooldown):
            mensagem = f"⏳ Calma aí, aventureiro! Aguarde **{error.retry_after:.1f}s** para conjurar isso novamente."
        
        # 2. Erro de Permissão (Usuário tentando usar comando de Admin)
        elif isinstance(error, app_commands.MissingPermissions):
            mensagem = "🚫 Você não tem os privilégios necessários para acessar este pergaminho (Falta de Permissão)."
            
        # 3. Erro de Check (Se você usar @app_commands.check e o usuário falhar)
        elif isinstance(error, app_commands.CheckFailure):
            mensagem = "🔒 Você não atende aos requisitos para usar este comando no momento."
        
        # 4. Qualquer outro erro não mapeado
        else:
            mensagem = "❌ Ocorreu um distúrbio na rede. O erro foi registrado para a auditoria."
            # Isso garante que o erro real ainda apareça no seu terminal para você consertar
            print(f"[LOG DE ERRO] Falha crítica no comando /{interaction.command.name}: {error}")

        # Envia a resposta de forma "efêmera" (apenas o usuário que errou consegue ler a mensagem)
        if interaction.response.is_done():
            await interaction.followup.send(mensagem, ephemeral=True)
        else:
            await interaction.response.send_message(mensagem, ephemeral=True)

    # --- TRATADOR DE COMANDOS COM PREFIXO CLASSICOS ( ex: !lore ) ---
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Ignora silenciosamente se o usuário digitar um comando que não existe
        if isinstance(error, commands.CommandNotFound):
            return 
            
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("⚠️ Faltou algum argumento na sua magia! Verifique a sintaxe e tente de novo.")
            
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫 Acesso negado pelo sistema de Governança.")
            
        else:
            # Erros graves vão para o terminal
            print(f"[LOG DE ERRO] Falha no comando {ctx.command}: {error}")

async def setup(bot):
    await bot.add_cog(TratamentoErros(bot))