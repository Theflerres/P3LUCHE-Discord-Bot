"""
Configuração central do P3LUCHE — constantes, IDs, caminhos e estado global.
"""
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
CLIENT_SECRET_FILE = os.path.join(os.getcwd(), "client_secret.json")
CREDENTIALS_PATH = os.path.join(LOG_FOLDER, "credentials.json")

# --- CANAIS & ROLES ---
USER_MUSIC_CHANNEL_ID = [1376724217747341322, 1281458252231737374, 1377042576787505204, 1472260791976857620]
WARN_CHANNEL_ID = 1349002209794195526
MOD_ROLE_IDS = [1444846159850901584, 1282147756814766132]
CREATOR_ID = 299323165937500160

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
CATCHES_SINCE_RESTART = {}
# Trava para proteger o contador em ambientes multi-thread
CATCHES_LOCK = threading.Lock()

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
