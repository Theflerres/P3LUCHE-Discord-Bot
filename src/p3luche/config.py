"""Configuração central do P3LUCHE.

Centraliza constantes, IDs, caminhos de arquivos locais, variáveis de ambiente
necessárias ao bot e algumas estruturas compartilhadas usadas por diferentes cogs.
"""
import asyncio
import os
import threading

import discord
from dotenv import load_dotenv

load_dotenv()

# --- TOKENS & CHAVES ---
TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_KEY")

if not TOKEN:
    print("ERRO CRÍTICO: DISCORD_TOKEN não definido no .env")

# --- BANCO & PASTAS ---
LOG_FOLDER = os.path.join(os.getcwd(), "database")
os.makedirs(LOG_FOLDER, exist_ok=True)

DB_PATH = os.path.join(LOG_FOLDER, "bot.db")
CREATOR_NAME = "theflerres"
DRIVE_FOLDER_ID = "1U8-Pz2YamB1OSP-wAaT8Wzw-3VOKM8Hc"
# Pasta exclusiva do Jukebox no Drive (evita misturar com outros fluxos de áudio).
# Configure via .env: JUKEBOX_DRIVE_FOLDER_ID=<id_da_pasta>
# Fallback para DRIVE_FOLDER_ID para não quebrar ambientes antigos.
JUKEBOX_DRIVE_FOLDER_ID = os.getenv("JUKEBOX_DRIVE_FOLDER_ID", DRIVE_FOLDER_ID)
CLIENT_SECRET_FILE = os.path.join(os.getcwd(), "client_secret.json")
CREDENTIALS_PATH = os.path.join(LOG_FOLDER, "credentials.json")

# --- CANAIS & ROLES ---
USER_MUSIC_CHANNEL_ID = [1376724217747341322, 1281458252231737374, 1377042576787505204, 1472260791976857620]
WARN_CHANNEL_ID = 1349002209794195526
MOD_ROLE_IDS = [1444846159850901584, 1282147756814766132]
CREATOR_ID = 299323165937500160

# Canal privado/staff para stack traces e detalhes técnicos de erro (NUNCA o
# mesmo canal usado para advertências públicas). Configure via .env:
# ERROR_LOG_CHANNEL_ID=<id_do_canal>. Sem essa variável, o bot não envia
# stack traces a canal nenhum do Discord (só grava em database/bot_erros.log)
# em vez de cair de volta para um canal público por engano.
_error_log_channel_env = os.getenv("ERROR_LOG_CHANNEL_ID")
ERROR_LOG_CHANNEL_ID = int(_error_log_channel_env) if _error_log_channel_env else None

# Canal onde o banner pontual de mudança de clima da pescaria (Tempestade
# Sombria / Brisa Dourada) é publicado. Configure via .env:
# FISHING_CHANNEL_ID=<id_do_canal>. Sem essa variável, o bot não envia o
# banner (o rich presence continua atualizando normalmente).
_fishing_channel_env = os.getenv("FISHING_CHANNEL_ID")
FISHING_CHANNEL_ID = int(_fishing_channel_env) if _fishing_channel_env else None

# Canal onde a mensagem de boas-vindas (on_member_join, Fase 8) é publicada.
# Configure via .env: WELCOME_CHANNEL_ID=<id_do_canal>. Sem essa variável, o
# bot simplesmente não envia boas-vindas (sem cair de volta pra nenhum canal
# por engano).
_welcome_channel_env = os.getenv("WELCOME_CHANNEL_ID")
WELCOME_CHANNEL_ID = int(_welcome_channel_env) if _welcome_channel_env else None

# --- LIMITES MÚSICA ---
MAX_SONG_SIZE_MB = 3000
STANDBY_TIMEOUT_MINUTES = 20

# --- RICH PRESENCE ---
CAT_FACES = ["₍^. .^₎⟆", "≽^•⩊•^≼", "/ᐠ - ˕ -マ", "(˶˃ᆺ˂˶)", "(=^･ω･^=)", "ฅ^•ﻌ•^ฅ"]
CAT_ACTIVITIES = [
    (discord.ActivityType.playing, "com novelo"),
    (discord.ActivityType.watching, "pássaros na janela"),
    (discord.ActivityType.listening, "ronronados"),
    (discord.ActivityType.competing, "corrida às 3h da manhã"),
    (discord.ActivityType.playing, "derrubando copos"),
    (discord.ActivityType.watching, "você dormir"),
    (discord.ActivityType.listening, "Música Lo-Fi"),
    (discord.ActivityType.custom, "Julgando humanos em silêncio"),
]

# --- EMOTES ---
EMOTE_FOGO = "<:fogo:1445100584528117931>"
EMOTE_MEDO = "<:assustador:1445100586424074292>"
EMOTE_CANSADO = "<:cansado:1445100588538003508>"

# --- YT-DLP (global legado; música usa também COOKIE_FILE em cogs/musica) ---
YDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "cookiefile": "cookies.txt",
}

# --- LOCKS & ESTADO EM MEMÓRIA ---
db_lock = threading.Lock()

# Contador em memória de pescas desde o último restart do bot.
# Usado para garantir a garrafa na 2ª pesca após reinício.
CATCHES_SINCE_RESTART: dict[int, tuple[int, float]] = {}
# Tempo de expiração para entradas inativas no contador.
CATCHES_TTL_SECONDS = 60 * 60 * 24  # 24 horas
# Trava assíncrona para proteger o contador em coroutines do bot.
CATCHES_LOCK = asyncio.Lock()

# Referência ao bot (preenchida em main.setup_hook) para cogs que precisam
# acessar a instância sem passar por todos os construtores legados.
BOT_INSTANCE = None


def set_bot_instance(bot):
    """Define a instância global do bot (uso interno / migração gradual)."""
    global BOT_INSTANCE
    BOT_INSTANCE = bot


def get_bot_instance():
    """Retorna a instância do bot registrada, ou None."""
    return BOT_INSTANCE
