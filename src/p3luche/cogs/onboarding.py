"""
Onboarding (Fase 8) — mensagem de boas-vindas ao entrar no servidor.

A falha mais comum reportada ("comandos não aparecem/não respondem") é o
usuário não ter clicado em "Adicionar App" pra si mesmo — isso acontece
inteiramente do lado do cliente Discord, antes da interação chegar ao bot.
Não existe evento que o bot consiga capturar nesse caso (nenhum
INTERACTION_CREATE é despachado), então a única solução é comunicação
preventiva: avisar antes que aconteça, aqui e em /ajuda.

Nunca em DM: usuários com privacidade restrita simplesmente não recebem a
mensagem, sem nenhum aviso de falha — canal público é o único jeito
confiável de garantir que a informação chegue.
"""
import discord
from discord.ext import commands

from config import WELCOME_CHANNEL_ID
from utils import log_to_gui

ADD_APP_STEPS = (
    "1️⃣ Clique no nome ou no avatar do **P3LUCHE** em qualquer mensagem dele "
    "(ou na lista de membros, à direita).\n"
    "2️⃣ No perfil que abrir, clique em **Adicionar App**.\n"
    "3️⃣ Escolha adicionar à sua conta e clique em **Autorizar**."
)


def build_welcome_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🎣 Bem-vindo(a) ao P3LUCHE!",
        description=(
            f"Olá, {member.mention}! Que bom te ver por aqui.\n\n"
            "Pra começar, é só usar **/eco pescar** — isso já cria sua conta "
            "automaticamente e te dá sua primeira pescaria, sem precisar repetir o comando."
        ),
        color=discord.Color.teal(),
    )
    embed.add_field(
        name="⚠️ Os comandos não aparecem (ou não respondem)?",
        value=(
            "Às vezes o Discord exige que você autorize o P3LUCHE pra sua própria conta, "
            "mesmo com o bot já estando no servidor. Se digitar `/` e não aparecer nada "
            "(ou o comando falhar sem explicação):\n\n"
            f"{ADD_APP_STEPS}\n\n"
            "Depois disso os comandos aparecem normalmente."
        ),
        inline=False,
    )
    embed.set_footer(text="Use /ajuda a qualquer momento para ver todos os comandos.")
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    return embed


class OnboardingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not WELCOME_CHANNEL_ID:
            return
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(WELCOME_CHANNEL_ID)
            except discord.HTTPException as e:
                log_to_gui(f"Falha ao buscar canal de boas-vindas: {e}", "ERROR")
                return
        try:
            await channel.send(embed=build_welcome_embed(member))
        except discord.HTTPException as e:
            log_to_gui(f"Falha ao enviar mensagem de boas-vindas: {e}", "ERROR")


async def setup(bot):
    await bot.add_cog(OnboardingCog(bot))
