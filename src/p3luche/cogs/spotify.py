"""
Spotify — integração com ingestão de áudio via YouTube (yt-dlp), reutilizando a pipeline da biblioteca.
"""
import re

import discord
from discord import app_commands

from utils import log_to_gui

# Import tardio evita dependência circular na carga do pacote
from cogs.musica import check_channel_permission, musica_group, process_slash_music_addition


def _spotify_track_url(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/(track|album|playlist)/", url, re.I))


def _message_spotify_to_youtube() -> str:
    return (
        "🎧 **Spotify detectado.** Eu não baixo direto do Spotify (direitos & DRM). "
        "Cole um **link do YouTube** com a mesma faixa, ou use `/musica adicionar` com o link do vídeo. "
        "*Sim, é chato. A culpa é dos humanos e dos contratos, não minha.*"
    )


# Comandos dedicados ao fluxo Spotify → orientação / YouTube, agrupados sob
# /musica (mesmo grupo de musica.py) por serem parte da mesma funcionalidade
# de biblioteca musical. A adição real de áudio continua centralizada em
# `process_slash_music_addition`. Não têm estado próprio, por isso são
# funções soltas anexadas ao musica_group já existente, em vez de uma Cog —
# mesmo padrão já usado pelos outros comandos de /musica.


@musica_group.command(
    name="spotify_add",
    description="Aceita um link Spotify e explica como obter o equivalente no YouTube para a biblioteca.",
)
@app_commands.describe(link="Link do Spotify (track/album/playlist)")
async def spotify_add(interaction: discord.Interaction, link: str):
    if not await check_channel_permission(interaction):
        return
    if not _spotify_track_url(link):
        await interaction.response.send_message(
            "❌ Isso não parece um link válido do Spotify. Tente `open.spotify.com/...`.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"{_message_spotify_to_youtube()}\n\n"
        f"**Seu link:** <{link}>\n"
        f"Quando tiver o link do YouTube, use: `/musica adicionar` com o vídeo.",
        ephemeral=True,
    )
    log_to_gui(f"{interaction.user.name} usou /musica spotify_add (Spotify → instruções YouTube).", "INFO")


@musica_group.command(
    name="spotify_pipe",
    description="Se você já tem o URL do YouTube equivalente ao Spotify, dispara a pipeline de download.",
)
@app_commands.describe(youtube_url="Link direto do YouTube (watch ou youtu.be)")
async def spotify_pipe(interaction: discord.Interaction, youtube_url: str):
    """Mesmo pipeline que `/musica adicionar` (yt-dlp → Drive → SQLite)."""
    if not await check_channel_permission(interaction):
        return
    if _spotify_track_url(youtube_url):
        await interaction.response.send_message(_message_spotify_to_youtube(), ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    await process_slash_music_addition(interaction, youtube_url)


async def setup(bot):
    pass
