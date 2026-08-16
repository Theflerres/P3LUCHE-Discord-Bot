"""Autorização centralizada para comandos administrativos do P3LUCHE.

Fonte única de verdade: qualquer comando que precise ser exclusivo do dono
do bot deve usar is_bot_owner() daqui, em vez de reimplementar a checagem
inline (padrão que já causou um bug de segurança real — ver /admin_quest,
corrigido na auditoria).
"""
import discord
from discord import app_commands

from config import CREATOR_ID


def is_bot_owner():
    """Bloqueia o comando no backend para qualquer usuário que não seja o
    Criador (CREATOR_ID). Isso é o que efetivamente impede a execução —
    visibilidade reduzida via default_permissions() é só cosmética.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == CREATOR_ID

    return app_commands.check(predicate)
