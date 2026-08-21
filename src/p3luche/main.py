"""Ponto de entrada do bot Discord.

Este módulo cria a instância do bot, registra os intents necessários,
carrega as cogs principais e inicializa a conexão com o banco SQLite.
A configuração do ambiente é feita via módulos auxiliares, e o fluxo de
startup inclui migrações de esquema e sincronização de comandos slash.
"""
import sys

# Reconfigura stdout/stderr para UTF-8 antes de qualquer outro import.
# Sem isso, em consoles Windows presos ao codepage legado (cp1252/"charmap"),
# qualquer print() ou log_to_gui() com emoji (ex: database.py, main.py)
# derruba o processo com UnicodeEncodeError assim que o log dispara —
# já aconteceu em produção. Corrige na fonte em vez de caçar emoji por emoji.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # stream sem suporte a reconfigure (ex: capturada em testes)

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
    try:
        from migration_v4 import migrate_to_normalized

        report = migrate_to_normalized()
        if report["migrated"] > 0:
            log_to_gui(
                f"Migracao v4: {report['migrated']} jogador(es) sincronizados.",
                "SUCCESS",
            )
    except Exception as e:
        log_to_gui(f"Migracao v4: {e}", "WARNING")
    set_bot_instance(bot)

    # Ordem de carregamento: primeiro os módulos que fornecem contexto e IA,
    # depois os que dependem de advertências ou integração com o resto do bot.
    extensions = [
        "cogs.lore_ai",
        "cogs.moderacao",
        "cogs.jukebox",
        "cogs.musica",
        "cogs.economia",
        "cogs.minigames",
        "cogs.ilha",
        "cogs.onboarding",
        "cogs.casino",
        "cogs.spotify",
        "cogs.sistema",
        "cogs.admin",
        "cogs.backup",
        "cogs.erros",
        "cogs.logs",
        "cogs.dashboard",
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
    log_to_gui("Bot desconectado; mantendo conexão com o banco para reconexão automática.", "INFO")

@bot.event
async def on_connect():
    if not hasattr(bot, "db_conn") or bot.db_conn is None:
        bot.db_conn = db_manager.connect()
        db_manager.migrate()
        log_to_gui("Reconectado e conexão com o banco reaberta.", "INFO")

if __name__ == "__main__":
    # O painel precisa envolver bot.run(): o Live do rich redireciona
    # stdout/stderr, e o setup_logging() do discord.py (chamado lá dentro)
    # captura sys.stderr no momento da construção do handler. Iniciar depois
    # deixaria os logs do discord.py escapando por fora do painel.
    # Desligado -> dashboard_session() é no-op e nada disso acontece.
    # Importado de dashboard_runtime, NÃO de cogs.dashboard: o load_extension
    # re-executa o módulo da extensão num objeto novo, então a sessão iniciada
    # aqui ficaria numa cópia que o cog nunca enxerga (o painel não desenhava).
    try:
        from cogs.dashboard_runtime import dashboard_session
    except Exception as e:  # painel indisponível nunca impede o bot de subir
        print(f"[painel] indisponível ({e}); seguindo sem ele.")
        dashboard_session = None

    try:
        if dashboard_session is None:
            bot.run(TOKEN)
        else:
            with dashboard_session():
                bot.run(TOKEN)
    except Exception as e:
        print(f"Erro fatal ao iniciar: {e}")
