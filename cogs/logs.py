import discord
from discord.ext import commands
from datetime import datetime

class SistemaLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # ⚠️ COLOQUE AQUI O ID DO CANAL DE TEXTO PRIVADO DOS ADMINS
        self.canal_auditoria_id = 1489330740876284206  

    async def enviar_log(self, embed: discord.Embed):
        """Função auxiliar para enviar o embed pro canal certo"""
        canal = self.bot.get_channel(self.canal_auditoria_id)
        if canal:
            await canal.send(embed=embed)
        else:
            print(f"[AVISO LORE] Canal de auditoria ({self.canal_auditoria_id}) não encontrado. Verifique o ID.")

    # --- LISTENER: MENSAGEM DELETADA ---
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        # Ignora mensagens apagadas na DM ou mensagens apagadas pelo próprio bot
        if not message.guild or message.author.bot:
            return

        embed = discord.Embed(
            title="🗑️ Pergaminho Destruído (Mensagem Deletada)",
            description=f"**Autor:** {message.author.mention}\n**Canal:** {message.channel.mention}",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        # O Discord tem um limite de 1024 caracteres em campos de embed
        # Pegamos até 1000 caracteres para não dar erro se o texto for gigante
        conteudo = message.content[:1000] if message.content else "[Sem texto / Apenas Mídia]"
        embed.add_field(name="Conteúdo Original:", value=conteudo, inline=False)
        
        await self.enviar_log(embed)

    # --- LISTENER: MENSAGEM EDITADA ---
    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        # Ignora bots e "falsas edições" (o Discord dispara esse evento as vezes só pra carregar a imagem de um link)
        if not before.guild or before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            title="📝 Relato Alterado (Mensagem Editada)",
            description=f"**Autor:** {before.author.mention}\n**Canal:** {before.channel.mention}\n[Ir para a mensagem]({after.jump_url})",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        
        antes = before.content[:1000] if before.content else "[Vazio]"
        depois = after.content[:1000] if after.content else "[Vazio]"
        
        embed.add_field(name="Antes:", value=antes, inline=False)
        embed.add_field(name="Depois:", value=depois, inline=False)
        
        await self.enviar_log(embed)

async def setup(bot):
    await bot.add_cog(SistemaLogs(bot))