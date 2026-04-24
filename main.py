"""
P3LUCHE — ponto de entrada. Carrega extensões (Cogs), banco SQLite e inicia o bot.
"""
import asyncio
import sqlite3
import threading
from datetime import datetime

import discord
from discord.ext import commands

from config import TOKEN, set_bot_instance
from database import db_manager
from utils import log_to_gui

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def setup_hook():
    bot.db_conn = db_manager.connect()
    db_manager.migrate()
    set_bot_instance(bot)

    # Ordem: Lore/Persona antes de moderação (advertência usa IA).
    extensions = [
        "cogs.lore_ai",
        "cogs.moderacao",
        "cogs.jukebox",
        "cogs.musica",
        "cogs.economia",
        "cogs.spotify",
        "cogs.sistema",
        "cogs.backup",
        "cogs.erros", 
        "cogs.logs",   
    ]
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            log_to_gui(f"Extensão carregada: {ext}", "SUCCESS")
        except Exception as e:
            log_to_gui(f"Falha ao carregar {ext}: {e}", "ERROR")


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    bot.start_time = datetime.now()
    log_to_gui(f"Bot Online: {bot.user}", "SUCCESS")

    try:
        cursor = bot.db_conn.cursor()
        cursor.execute(
            "ALTER TABLE economy ADD COLUMN last_fish_time TEXT DEFAULT '1970-01-01T00:00:00'"
        )
        bot.db_conn.commit()
        print("✅ Banco de dados atualizado: Coluna 'last_fish_time' adicionada.")
    except sqlite3.OperationalError:
        pass

    try:
        synced = await bot.tree.sync()
        print(f"✅ Sincronizado {len(synced)} comandos com sucesso!")
        log_to_gui(f"Sincronizado {len(synced)} comandos.", "INFO")
    except Exception as e:
        print(f"❌ Erro ao sincronizar: {e}")

    from cogs.musica import _populate_normalized_titles_if_empty, rebuild_database_from_drive

    _populate_normalized_titles_if_empty()
    try:
        cursor = bot.db_conn.cursor()
        if cursor.execute("SELECT COUNT(*) FROM music_cache").fetchone()[0] == 0:
            threading.Thread(
                target=lambda: asyncio.run(rebuild_database_from_drive(True))
            ).start()
    except Exception:
        pass


@bot.event
async def on_disconnect():
    if hasattr(bot, "db_conn") and bot.db_conn:
        try:
            bot.db_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Erro fatal ao iniciar: {e}")
