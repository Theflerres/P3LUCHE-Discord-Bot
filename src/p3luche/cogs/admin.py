"""
Comandos administrativos exclusivos do dono do bot (Criador).

Escopo desta etapa (rede de segurança mínima antes da migração de economia
para a camada v4): só /admin economia consultar e /admin economia corrigir.
Dar/remover dinheiro como atalho dedicado, resets de progressão, debug e
inspeção genérica de tabelas ficam para a Fase 7 completa.

Autorização: is_bot_owner() (permissions.py) é a ÚNICA fonte de verdade —
nenhum comando aqui deve reimplementar checagem de dono inline.
default_permissions(administrator=True) no grupo é só redução de ruído na
UI do Discord (escondido de quem não é admin do servidor); não substitui
a checagem de backend.
"""
import discord
from discord import app_commands
from discord.ext import commands

from config import get_bot_instance
from economy_db import get_wallet, modify_wallet
from permissions import is_bot_owner


class ConfirmView(discord.ui.View):
    """Confirmação genérica sim/não para ações de admin com efeito real.

    Nenhum componente de confirmação genérico existia no bot antes desta
    etapa (o mais próximo, AuctionApprovalView em minigames.py, é
    específico do fluxo de leilão) — esta view segue o mesmo padrão de
    interaction_check + desabilitar botões ao clicar/no timeout já usado
    em BlackjackView, CrashView e AuctionApprovalView.
    """

    def __init__(self, author_id: int, timeout: float = 15):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Só quem pediu a confirmação pode responder.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        self.confirmed = False
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class AdminCog(commands.Cog):
    """Comandos exclusivos do Criador. Nunca exposto como comando comum."""

    admin_group = app_commands.Group(
        name="admin",
        description="Ferramentas administrativas (somente o Criador).",
        default_permissions=discord.Permissions(administrator=True),
    )
    economia_group = app_commands.Group(
        name="economia",
        description="Consulta e correção de economia de jogadores.",
        parent=admin_group,
    )

    def __init__(self, bot):
        self.bot = bot

    @economia_group.command(name="consultar", description="Consulta a economia de um jogador (somente leitura).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a consultar")
    async def economia_consultar(self, interaction: discord.Interaction, usuario: discord.Member):
        conn = get_bot_instance().db_conn
        wallet = get_wallet(conn, usuario.id)

        embed = discord.Embed(title=f"🔍 Economia de {usuario.display_name}", color=discord.Color.blue())
        embed.add_field(name="Carteira", value=f"{wallet} Sachês", inline=False)
        embed.set_footer(text=f"ID: {usuario.id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @economia_group.command(name="corrigir", description="Corrige um campo da economia de um jogador (com confirmação).")
    @is_bot_owner()
    @app_commands.describe(usuario="Jogador a corrigir", campo="Campo a corrigir", valor="Novo valor absoluto do campo")
    @app_commands.choices(campo=[app_commands.Choice(name="Carteira (wallet)", value="wallet")])
    async def economia_corrigir(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        campo: app_commands.Choice[str],
        valor: int,
    ):
        conn = get_bot_instance().db_conn

        # Só "wallet" tem helper atômico pronto na camada v4 hoje
        # (get_wallet/modify_wallet). Outros campos (scrap, guild_rank,
        # fish_count...) exigem helpers equivalentes ainda não escritos —
        # fora do escopo desta etapa mínima.
        if campo.value != "wallet":
            return await interaction.response.send_message(
                f"❌ Campo `{campo.value}` ainda não suportado.", ephemeral=True
            )

        atual = get_wallet(conn, usuario.id)
        descricao = f"Carteira: **{atual}** → **{valor}** Sachês"

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ **Confirmar correção manual?**\n{descricao}\n"
            f"Jogador: {usuario.mention} (ID: {usuario.id})",
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.confirmed:
            await interaction.edit_original_response(
                content="🚫 Correção cancelada (recusada ou expirou sem resposta).", view=None
            )
            return

        delta = valor - atual
        novo_saldo = modify_wallet(conn, usuario.id, delta, usuario.display_name)
        await interaction.edit_original_response(
            content=f"✅ Corrigido. Carteira de {usuario.mention} agora é **{novo_saldo}** Sachês.",
            view=None,
        )


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
