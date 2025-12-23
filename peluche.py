"""
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
PROJETO: P3LUCHE - Sistema Operacional de Comunidade & RPG
AUTOR:   @Theflerres
DATA:    Dezembro de 2024 - Dezembro de 2025 (v2.5)
LICENÇA: Proprietária / Todos os Direitos Reservados

DESCRIÇÃO:
Bot multifuncional desenvolvido em Python com integração de IA (Gemini),
Visualização de Grafos (NetworkX), Gestão de Música (Drive API) e Governança
de Dados (SQLite).

AVISO LEGAL:
Este código fonte é propriedade intelectual de @Theflerres.
A cópia, redistribuição ou uso comercial sem autorização expressa do autor
é proibida. Este repositório serve apenas para fins de portfólio e estudo.
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
"""

import sys
import threading
import asyncio
import random
import string
import gc
from datetime import datetime, timedelta
from PIL import Image
import requests
from io import BytesIO
import sqlite3
import os
import re
import tempfile
import psutil
from dotenv import load_dotenv
import networkx as nx
import matplotlib.pyplot as plt
import scipy # Necessário para o layout do gráfico não quebrar
import textwrap
import difflib 
# --- NOVAS LIBS ---
import pypdf
import docx
# --- IMPORTS DO DISCORD ---
import discord
from discord import app_commands
from discord.ext import commands, tasks
# --- IMPORTS DO YOUTUBE/GOOGLE ---
from yt_dlp import YoutubeDL
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
# --- IMPORT DA IA (GEMINI) ---
import google.generativeai as genai

db_lock = threading.Lock()

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_KEY")

if not TOKEN:
    print("ERRO CRÍTICO: DISCORD_TOKEN não definido no .env")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, loop=None)

## --- CONFIGURAÇÕES ---

USER_MUSIC_CHANNEL_ID = [1376724217747341322, 1281458252231737374, 1377042576787505204]
WARN_CHANNEL_ID = 1349002209794195526
MOD_ROLE_IDS = [1444846159850901584, 1282147756814766132]
CREATOR_ID = 299323165937500160 
MAX_SONG_SIZE_MB = 40
STANDBY_TIMEOUT_MINUTES = 20 

# --- EMOTES ---
EMOTE_FOGO = "<:fogo:1445100584528117931>"
EMOTE_MEDO = "<:assustador:1445100586424074292>"
EMOTE_CANSADO = "<:cansado:1445100588538003508>"

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
    (discord.ActivityType.custom, "Julgando humanos em silêncio") 
]

LOG_FOLDER = os.path.join(os.getcwd(), "database") 
os.makedirs(LOG_FOLDER, exist_ok=True)

DB_PATH = os.path.join(LOG_FOLDER, "bot.db")
CREATOR_NAME = "theflerres"
DRIVE_FOLDER_ID = "1U8-Pz2YamB1OSP-wAaT8Wzw-3VOKM8Hc"
CLIENT_SECRET_FILE = os.path.join(os.getcwd(), "client_secret.json")
CREDENTIALS_PATH = os.path.join(LOG_FOLDER, "credentials.json")

# --- FUNÇÃO DE LOG MODIFICADA PARA O TERMINAL ---
def log_to_gui(message, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    # Cores ANSI para ficar bonito no terminal do Linux
    colors = {
        "INFO": "\033[94m",    # Azul
        "SUCCESS": "\033[92m", # Verde
        "ERROR": "\033[91m",   # Vermelho
        "WARNING": "\033[93m", # Amarelo
        "WAKEUP": "\033[96m",  # Ciano
        "SLEEP": "\033[90m"    # Cinza
    }
    reset = "\033[0m"
    color_code = colors.get(level, "\033[97m") # Branco padrão
    
    print(f"{color_code}[{timestamp}] [{level}] {message}{reset}")

async def extract_text_from_attachment(attachment: discord.Attachment) -> str:
    filename = attachment.filename.lower()
    try:
        file_bytes = await attachment.read()
        file_stream = BytesIO(file_bytes)
        extracted_text = ""
        if filename.endswith('.pdf'):
            reader = pypdf.PdfReader(file_stream)
            for page in reader.pages: extracted_text += page.extract_text() + "\n"
        elif filename.endswith('.docx'):
            doc = docx.Document(file_stream)
            extracted_text = "\n".join([para.text for para in doc.paragraphs])
        elif filename.endswith('.txt') or filename.endswith('.md'):
            extracted_text = file_bytes.decode('utf-8')
        else: return ""
        return extracted_text.strip()
    except Exception as e:
        log_to_gui(f"Erro ao ler arquivo {filename}: {e}", "ERROR")
        return f"[Erro ao ler arquivo: {e}]"

# --- FUNÇÃO DE SEGURANÇA (NOVA) ---
def sanitize_text(text: str) -> str:
    """Limpa o texto de entrada para evitar injeções simples e caracteres nulos."""
    if not text: return ""
    # Remove caracteres nulos e espaços extras
    clean = text.replace('\x00', '').strip()
    # Limite rígido de tamanho para não estourar contexto ou custos
    return clean[:1500]


# --- CLASSE DE PERSONALIDADE (P3LUCHE - MODO PAI/CRIADOR) ---
class P3luchePersona(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_channels = USER_MUSIC_CHANNEL_ID 
        self.last_activity = datetime.now()
        self.is_standby = False
        
        # PERSONALIDADE BASE (Sarcástica)
        self.persona_base = (
            "Você é o P3LUCHE, o gato mascote do servidor. "
            "Sua personalidade: Temperamental, imprevisível e felino. '8 ou 80'. "
            "Diretrizes: SEJA BREVE. TOM: Sarcástico/Felino. ZERO TECH. "
            "Se perguntarem de lore, use o contexto fornecido."
        )

        if GEMINI_KEY:
            try:
                genai.configure(api_key=GEMINI_KEY)
                self.model = genai.GenerativeModel('gemma-3-12b-it')
                log_to_gui(f"IA Conectada: Gemma 3 (Reconhecimento de Criador Ativo).", "SUCCESS")
            except Exception as e:
                log_to_gui(f"Erro ao configurar IA: {e}", "ERROR")
                self.model = None
        else:
            self.model = None

        self.emote_fogo = EMOTE_FOGO
        self.emote_medo = EMOTE_MEDO
        self.emote_cansado = EMOTE_CANSADO
        
        self.random_event_loop.start()
        self.standby_check_loop.start()
        self.status_rotation_loop.start()

    def cog_unload(self):
        self.random_event_loop.cancel()
        self.standby_check_loop.cancel()
        self.status_rotation_loop.cancel()

    # --- LÓGICA DE STANDBY ---
    async def register_activity(self):
        self.last_activity = datetime.now()
        if self.is_standby:
            self.is_standby = False
            log_to_gui("Acordando...", "WAKEUP")
            await self.update_rich_presence()
            if not self.random_event_loop.is_running(): self.random_event_loop.start()

    @tasks.loop(minutes=5)
    async def status_rotation_loop(self):
        if not self.is_standby: await self.update_rich_presence()

    async def update_rich_presence(self):
        face = random.choice(CAT_FACES)
        act_type, act_name = random.choice(CAT_ACTIVITIES)
        status = f"{act_name} {face}"
        act_obj = discord.Activity(type=act_type if act_type != discord.ActivityType.custom else discord.ActivityType.custom, name="custom" if act_type == discord.ActivityType.custom else status, state=status if act_type == discord.ActivityType.custom else None)
        await self.bot.change_presence(status=discord.Status.online, activity=act_obj)

    @tasks.loop(minutes=1)
    async def standby_check_loop(self):
        if self.is_standby: return
        if (datetime.now() - self.last_activity).total_seconds() > (STANDBY_TIMEOUT_MINUTES * 60):
            self.is_standby = True
            log_to_gui(f"Standby iniciado.", "SLEEP")
            await self.bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.custom, name="custom", state="💤 Zzz..."))
            self.random_event_loop.cancel()
            gc.collect()

    @commands.Cog.listener()
    async def on_interaction(self, interaction): await self.register_activity()
    @status_rotation_loop.before_loop
    async def before_status(self): await self.bot.wait_until_ready()
    @standby_check_loop.before_loop
    async def before_standby(self): await self.bot.wait_until_ready()
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if not isinstance(error, commands.CommandNotFound): print(f"Erro: {error}")

    # --- HELPERS DB ---
    def get_server_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT content FROM server_lore ORDER BY created_at DESC LIMIT 15").fetchall()
            return "\n".join([f"- {x[0]}" for x in r[::-1]]) if r else ""
        except: return ""
    
    def get_all_players_with_lore(self):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT DISTINCT target_name, character_name FROM player_lore").fetchall()
            return "\n".join([f"- {n} ({c or '?'})" for n,c in r]) if r else "Ninguém."
        except: return ""

    def get_player_lore(self, tid):
        try:
            r = self.bot.db_conn.cursor().execute("SELECT content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC LIMIT 10", (tid,)).fetchall()
            return (f"PERSONAGEM: {r[0][1]}\n" + "\n".join([f"- {x[0]}" for x in r[::-1]])) if r else ""
        except: return ""

    async def split_and_send(self, message, text):
        if len(text) <= 2000: await message.reply(text)
        else: 
            for c in [text[i:i+1900] for i in range(0, len(text), 1900)]: await message.channel.send(c)

    @tasks.loop(minutes=45) 
    async def random_event_loop(self):
        if not self.allowed_channels or random.random() > 0.2: return
        try:
            ch = self.bot.get_channel(random.choice(self.allowed_channels))
            if ch: await ch.send(random.choice(["Tédio...", "Miau.", "*Julgando.*", "Zzz...", "Cadê meu sachê?"]))
        except: pass
    @random_event_loop.before_loop
    async def before_random(self): await self.bot.wait_until_ready()

   # --- CHAT COM IA & LÓGICA DE PAI (REFINADO FASE 3) ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        is_mentioned = self.bot.user.mentioned_in(message)
        if not is_mentioned: return

        # TRAVA DE SEGURANÇA: SÓ STAFF (Mantido conforme original)
        if not any(r.id in MOD_ROLE_IDS for r in message.author.roles): return

        await self.register_activity()
        
        # 1. Limpeza e Sanitização do Input (USANDO A FUNÇÃO NOVA)
        raw_content = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        content = sanitize_text(raw_content) 

        if not content: return # Ignora mensagens vazias ou só com caracteres nulos

        is_creator = message.author.id == CREATOR_ID
        
        # 2. Lógica de Gravar Memória (Agora salva is_active=1 por padrão no schema)
        if re.search(r'\b(lembre-se que|anote que)\b', content.lower()):
            try:
                c = re.sub(r'(lembre-se\s+que|anote\s+que)', '', content, flags=re.IGNORECASE).strip()
                # Adicionado coluna 'tag' e 'importance' do schema novo (default null/1)
                self.bot.db_conn.cursor().execute(
                    "INSERT INTO user_memories (user_id, user_name, memory_text, is_active) VALUES (?, ?, ?, 1)", 
                    (message.author.id, message.author.name, c)
                )
                self.bot.db_conn.commit()
                
                if is_creator:
                    await message.add_reaction("💙")
                    await message.reply(f"Anotado no meu núcleo, pai! 😺")
                else:
                    await message.add_reaction("💾")
                    await message.reply("Tá, guardei na memória.")
            except Exception as e:
                log_to_gui(f"Erro ao salvar memória: {e}", "ERROR")
            return

        # 3. Geração de Resposta (IA)
        if self.model:
            async with message.channel.typing():
                try:
                    # CONTEXTO DE LORE (MUNDO)
                    ctx_lore = f"\n[SERVER LORE]:\n{self.get_server_lore()}"
                    
                    # CONTEXTO DINÂMICO (Se pedir lista ou detalhes)
                    if any(x in content.lower() for x in ["quem", "lista", "lore"]):
                        ctx_lore += f"\n[LISTA]:\n{self.get_all_players_with_lore()}"
                    
                    # CONTEXTO DE OUTROS PLAYERS
                    for m in message.mentions:
                        if m.id != self.bot.user.id and (l := self.get_player_lore(m.id)): 
                            ctx_lore += f"\n[LORE {m.name}]:\n{l}"
                    
                    # 4. CONTEXTO DE MEMÓRIA PESSOAL (REFATORADO)
                    # Agora busca apenas memórias ativas (is_active = 1)
                    mem_rows = self.bot.db_conn.cursor().execute(
                        "SELECT memory_text FROM user_memories WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 5", 
                        (message.author.id,)
                    ).fetchall()
                    
                    user_memories = ""
                    if mem_rows:
                        # row[0] funciona, mas row['memory_text'] é mais seguro com row_factory
                        mem_list = [m['memory_text'] for m in mem_rows] 
                        user_memories = "\n[O QUE SEI SOBRE VOCÊ]:\n" + "\n- ".join(mem_list)

                    # DEFINE A PERSONALIDADE
                    if is_creator:
                        persona_ajustada = (
                            f"{self.persona_base}\n"
                            "IMPORTANTE: O usuário atual é seu CRIADOR/PAI (theflerres).\n"
                            "Com ele, seja doce, carinhoso, obediente e leal.\n"
                            "Use emojis fofos e demonstre afeto."
                        )
                    else:
                        persona_ajustada = (
                            f"{self.persona_base}\n"
                            "IMPORTANTE: O usuário atual é apenas um humano comum.\n"
                            "Seja temperamental, '8 ou 80', e levemente sarcástico/arrogante."
                        )

                    # PROMPT FINAL REFINADO
                    final_prompt = (
                        f"{persona_ajustada}\n\n"
                        f"DADOS DO MUNDO:{ctx_lore}\n"
                        f"{user_memories}\n\n"
                        f"USUÁRIO ({message.author.name}) DIZ: {content}\n"
                        f"RESPOSTA DO P3LUCHE:"
                    )
                    
                    response = await self.model.generate_content_async(final_prompt)
                    await self.split_and_send(message, response.text)

                except Exception as e:
                    if "429" in str(e): await message.reply("Cota excedida. (Gemma cansou)")
                    else: 
                        log_to_gui(f"Erro na IA: {e}", "ERROR")
                        await message.reply(" *Tosse bola de pelos* (Erro no processamento).")

# --- SLASH COMMANDS: ADVERTÊNCIA ---
# --- SLASH COMMANDS: ADVERTÊNCIA (ATUALIZADO FASE 2) ---
# --- SLASH COMMANDS: ADVERTÊNCIA (CORRIGIDO PARA LATÊNCIA) ---
@bot.tree.command(name="advertencia", description="Registra uma advertência (Somente Moderadores).")
@app_commands.describe(usuario="O usuário infrator", motivo="Motivo da advertência", prova_imagem="Print/Imagem (Opcional)", prova_texto="Link ou texto da prova (Opcional)")
async def slash_advertencia(interaction: discord.Interaction, usuario: discord.Member, motivo: str, prova_imagem: discord.Attachment = None, prova_texto: str = None):
    # 1. BLINDAGEM CONTRA LAG (Defer no topo)
    # Ganhamos 15 minutos para processar o resto sem dar erro de "Unknown Interaction"
    await interaction.response.defer() 

    # 2. Verifica Canal
    if interaction.channel_id != WARN_CHANNEL_ID:
        # Como já demos defer, usamos followup.send em vez de response.send_message
        await interaction.followup.send(f"🚫 Comando permitido apenas no canal <#{WARN_CHANNEL_ID}>.", ephemeral=True)
        return

    # 3. Verifica Cargo
    has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
    if not has_role:
        await interaction.followup.send("🚫 Acesso Negado. Apenas moderadores.", ephemeral=True)
        return

    # 4. Processa Provas
    proof_final = "Nenhuma prova anexada."
    if prova_imagem: proof_final = prova_imagem.url
    elif prova_texto: proof_final = prova_texto
    if prova_imagem and prova_texto: proof_final = f"{prova_texto}\n{prova_imagem.url}"

    # 5. Salva no DB
    cursor = bot.db_conn.cursor()
    cursor.execute("""
        INSERT INTO warnings (user_id, user_name, moderator_id, moderator_name, reason, proof)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (usuario.id, usuario.name, interaction.user.id, interaction.user.name, motivo, proof_final))
    bot.db_conn.commit()

    # 6. Contagem Inteligente (SÓ CONTA ATIVOS!)
    count = cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ? AND status = 'active'", (usuario.id,)).fetchone()[0]
    history = cursor.execute("SELECT reason FROM warnings WHERE user_id = ? AND status = 'active'", (usuario.id,)).fetchall()

    # 7. IA Analisa Punição
    suggestion = "Nenhuma ação automática sugerida."
    color = discord.Color.orange()
    
    if count >= 4:
        color = discord.Color.red()
        suggestion = "⚠️ **LIMITE DE 4 WARNS ATIVOS ATINGIDO.** Sugestão: **Ban/Kick**."
        
        # Tenta usar a IA
        cog = bot.get_cog("P3luchePersona")
        if cog and cog.model:
            hist_str = ", ".join([h[0] for h in history])
            try:
                prompt = f"O usuário {usuario.name} atingiu 4 advertências ativas. Histórico recente: {hist_str}. Última infração: {motivo}. Como moderador robô, sugira uma punição curta e severa."
                ai_resp = await cog.model.generate_content_async(prompt)
                suggestion = f"🤖 **Análise P3LUCHE:** {ai_resp.text}"
            except: pass

    # 8. Cria Nota Fiscal (Embed)
    embed = discord.Embed(title="🧾 REGISTRO DE ADVERTÊNCIA (Nota Fiscal)", color=color, timestamp=datetime.now())
    embed.add_field(name="Infrator", value=f"{usuario.mention}\n(ID: {usuario.id})", inline=True)
    embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
    embed.add_field(name="Contagem Ativa", value=f"**{count}/4**", inline=True)
    embed.add_field(name="📄 Motivo", value=motivo, inline=False)
    embed.add_field(name="🔗 Provas", value=proof_final, inline=False)
    embed.add_field(name="⚖️ Veredito/Sugestão", value=suggestion, inline=False)
    embed.set_footer(text="Sistema de Justiça P3LUCHE v2.2 (Governança Ativa)")

    # Envia a resposta final
    await interaction.followup.send(content=usuario.mention, embed=embed)

    # 9. Envia na DM do Usuário
    try:
        dm_embed = embed.copy()
        dm_embed.title = "🚨 VOCÊ RECEBEU UMA ADVERTÊNCIA"
        dm_embed.description = "Seu comportamento foi registrado. Avisos acumulados podem levar a banimento."
        await usuario.send(embed=dm_embed)
    except:
        # Usamos followup aqui também caso precise avisar erro
        pass

# --- NOVOS COMANDOS DE GERENCIAMENTO DE WARNS ---
# --- COMANDO HISTÓRICO (ATUALIZADO FASE 2) ---
@bot.tree.command(name="historico", description="Ver o histórico completo (Ativos e Perdoados).")
@app_commands.describe(usuario="O usuário para consultar")
async def historico_warns(interaction: discord.Interaction, usuario: discord.Member):
    has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
    if not has_role:
        await interaction.response.send_message("🚫 Acesso restrito a moderadores.", ephemeral=True)
        return

    cursor = bot.db_conn.cursor()
    # Agora selecionamos também o status e quem revogou
    rows = cursor.execute("""
        SELECT id, moderator_name, reason, created_at, proof, status, revoked_by 
        FROM warnings 
        WHERE user_id = ? 
        ORDER BY created_at DESC
    """, (usuario.id,)).fetchall()

    if not rows:
        await interaction.response.send_message(f"✅ Ficha limpa! O usuário **{usuario.name}** nunca foi advertido.", ephemeral=True)
        return

    # Contagem separada
    active_count = sum(1 for r in rows if r['status'] == 'active')
    revoked_count = sum(1 for r in rows if r['status'] != 'active')

    embed = discord.Embed(title=f"📂 Ficha Criminal: {usuario.name}", color=discord.Color.orange())
    embed.set_thumbnail(url=usuario.avatar.url if usuario.avatar else None)
    embed.set_footer(text=f"Ativos: {active_count} | Perdoados: {revoked_count} | ID: {usuario.id}")

    description_text = ""
    for row in rows:
        # Pega os dados usando nomes (graças ao row_factory configurado na Fase 1)
        w_id = row['id']
        mod = row['moderator_name']
        reason = row['reason']
        date = row['created_at']
        status = row['status']
        
        icon = "🔴" if status == 'active' else "🟢"
        status_text = "**ATIVO**" if status == 'active' else f"~REVOGADO por {row['revoked_by']}~"
        
        proof_display = "[Prova]" if "http" in row['proof'] else "Texto"

        entry = f"{icon} **ID: {w_id}** | {date}\n👮 {mod} | ⚖️ {status_text}\n📝 {reason}\n🔗 {proof_display}\nFAILED_SEPARATOR"
        description_text += entry
    
    chunks = description_text.split("FAILED_SEPARATOR")
    for chunk in chunks:
        if chunk.strip():
            embed.add_field(name="➖ Registro ➖", value=chunk, inline=False)

    await interaction.response.send_message(embed=embed)












# --- COMANDO PERDOAR (ATUALIZADO FASE 2 - SOFT DELETE) ---
@bot.tree.command(name="perdoar", description="Revoga uma advertência (Soft Delete).")
@app_commands.describe(id_advertencia="O ID da advertência")
async def remover_warn(interaction: discord.Interaction, id_advertencia: int):
    has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
    if not has_role:
        await interaction.response.send_message("🚫 Acesso restrito a moderadores.", ephemeral=True)
        return

    cursor = bot.db_conn.cursor()
    
    # Verifica se existe e se já não está revogada
    check = cursor.execute("SELECT user_id, user_name, status FROM warnings WHERE id = ?", (id_advertencia,)).fetchone()
    if not check:
        await interaction.response.send_message(f"❌ ID **{id_advertencia}** não encontrado.", ephemeral=True)
        return
    
    user_id, user_name, current_status = check['user_id'], check['user_name'], check['status']

    if current_status != 'active':
        await interaction.response.send_message(f"⚠️ Essa advertência já foi revogada anteriormente.", ephemeral=True)
        return

    # SOFT DELETE: Atualiza para 'revoked' em vez de deletar
    try:
        cursor.execute("""
            UPDATE warnings 
            SET status = 'revoked', 
                revoked_by = ?, 
                revoked_at = ? 
            WHERE id = ?
        """, (interaction.user.name, datetime.now(), id_advertencia))
        bot.db_conn.commit()

        # Verifica nova contagem ATIVA
        new_count = cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0]

        embed = discord.Embed(title="⚖️ INDULTO CONCEDIDO (Revogação)", color=discord.Color.green(), timestamp=datetime.now())
        embed.add_field(name="Beneficiário", value=f"{user_name}", inline=True)
        embed.add_field(name="Autor do Perdão", value=interaction.user.mention, inline=True)
        embed.add_field(name="ID Revogado", value=str(id_advertencia), inline=True)
        embed.add_field(name="Nova Contagem Ativa", value=f"**{new_count}/4**", inline=False)
        embed.set_footer(text="O registro foi mantido no histórico, mas não conta mais para punição.")

        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"Erro ao revogar: {e}", ephemeral=True)

# --- FUNÇÕES E COMANDOS DE MÚSICA  ---

def normalize_title(title: str) -> str:
    if not title: return ""
    norm_title = title.lower();
    norm_title = re.sub(r'\([^)]*\)|\[[^\]]*\]', '', norm_title)
    keywords = ['official music video', 'music video', 'official video', 'official audio','lyric video', 'lyrics', 'legendado', 'tradução', 'traduzido', 'hd', '4k','hq', 'clipe oficial', 'vídeo oficial', 'áudio oficial', 'full album', 'ao vivo', 'live','(', ')', '[', ']', '{', '}', '|', '-', '_', '"', "'"]
    for keyword in keywords: norm_title = norm_title.replace(keyword, '')
    return re.sub(r'\s+', ' ', norm_title).strip()

def _populate_normalized_titles_if_empty():
    cursor = bot.db_conn.cursor()
    all_songs = cursor.execute("SELECT id, title FROM music_cache WHERE normalized_title IS NULL OR normalized_title = ''").fetchall()
    if not all_songs: return
    count = 0
    for song_id, song_title in all_songs:
        if song_title:
            norm_title = normalize_title(song_title)
            cursor.execute("UPDATE music_cache SET normalized_title = ? WHERE id = ?", (norm_title, song_id))
            count += 1
    if count > 0: bot.db_conn.commit()
    log_to_gui(f"Normalização de {count} títulos concluída.")

def format_timedelta(delta: timedelta) -> str:
    days, rem = divmod(delta.total_seconds(), 86400);
    hours, rem = divmod(rem, 3600); minutes, _ = divmod(rem, 60)
    days, hours, minutes = int(days), int(hours), int(minutes)
    parts = []
    if days > 0: parts.append(f"{days} dia{'s' if days > 1 else ''}")
    if hours > 0: parts.append(f"{hours} hora{'s' if hours > 1 else ''}")
    if minutes > 0: parts.append(f"{minutes} minuto{'s' if minutes > 1 else ''}")
    return ", ".join(parts) if parts else "alguns segundos"

async def check_channel_permission(interaction: discord.Interaction) -> bool:
    if isinstance(interaction.channel, discord.DMChannel): return True
    if not USER_MUSIC_CHANNEL_ID: return True
    if interaction.channel.id in USER_MUSIC_CHANNEL_ID: return True
    allowed_mentions = " ".join([f"<#{cid}>" for cid in USER_MUSIC_CHANNEL_ID])
    msg = f"🚫 **Comando não permitido aqui!** Use: {allowed_mentions}"
    if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
    else: await interaction.response.send_message(msg, ephemeral=True)
    return False

# --- VIEWS DA BIBLIOTECA ---
class PaginationView(discord.ui.View):
    def __init__(self, data, sort_order_text):
        super().__init__(timeout=300)
        self.data, self.sort_order_text = data, sort_order_text
        self.items_per_page, self.current_page = 10, 1
        self.total_pages = (len(data) - 1) // self.items_per_page + 1

    async def create_embed(self):
        start, end = (self.current_page - 1) * self.items_per_page, self.current_page * self.items_per_page
        page_data = self.data[start:end]
        description = []
        for i, (title, link, duration) in enumerate(page_data):
            duration_val = duration if duration is not None else 0
            duration_str = f" ({duration_val // 60}:{duration_val % 60:02d})" if duration_val else ""
            display_title = (title[:65] + '...') if len(title) > 68 else title
            description.append(f"**{i+start+1}. {display_title}**{duration_str}\n[Link Direto]({link})")
        
        embed = discord.Embed(title=f"📚 Biblioteca ({self.sort_order_text})", description="\n".join(description) or "Nenhuma música encontrada.", color=discord.Color.gold())
        embed.set_footer(text=f"Página {self.current_page}/{self.total_pages}")
        return embed

    async def update_message(self, interaction: discord.Interaction):
        self.children[0].disabled = self.current_page == 1
        self.children[1].disabled = self.current_page == self.total_pages
        await interaction.response.edit_message(embed=await self.create_embed(), view=self)

    @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.blurple, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1; await self.update_message(interaction)

    @discord.ui.button(label="Próxima ➡️", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1; await self.update_message(interaction)

# --- VIEWS DA BIBLIOTECA (ATUALIZADO FASE 2.2) ---
class LibrarySortView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(placeholder="Escolha como organizar a biblioteca...",
        options=[
            discord.SelectOption(label="Alfabética (A-Z)", value="title_asc", emoji="🔡"),
            discord.SelectOption(label="Alfabética (Z-A)", value="title_desc", emoji="🔠"),
            discord.SelectOption(label="Mais Recentes", value="created_at_desc", emoji="⏳"),
            discord.SelectOption(label="Mais Antigas", value="created_at_asc", emoji="⌛"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        sort_choice = select.values[0]
        sort_map = {
            "title_asc": ("Ordem Alfabética (A-Z)", "normalized_title", "ASC"), 
            "title_desc": ("Ordem Alfabética (Z-A)", "normalized_title", "DESC"), 
            "created_at_desc": ("Mais Recentes", "created_at", "DESC"), 
            "created_at_asc": ("Mais Antigas", "created_at", "ASC")
        }
        sort_text, sort_column, sort_order = sort_map[sort_choice]
        
        cursor = bot.db_conn.cursor()
        if sort_column == "created_at": order_col = "COALESCE(created_at, '1970-01-01')"
        else: order_col = sort_column
        
        # ATUALIZAÇÃO FASE 2.2: Filtrar apenas is_active = 1
        query = f"SELECT title, drive_link, duration FROM music_cache WHERE title IS NOT NULL AND is_active = 1 ORDER BY {order_col} {sort_order}"
        all_songs = cursor.execute(query).fetchall()
        
        if not all_songs:
            await interaction.response.edit_message(content="A biblioteca de músicas está vazia (ou todas estão ocultas).", embed=None, view=None)
            return
        
        log_to_gui(f"{interaction.user.name} abriu a biblioteca (ordem: {sort_text}).")
        pagination_view = PaginationView(all_songs, sort_text)
        if pagination_view.total_pages <= 1: pagination_view.children[1].disabled = True
        
        embed = await pagination_view.create_embed()
        await interaction.response.edit_message(embed=embed, view=pagination_view)

# --- VIEW DE PAGINAÇÃO PARA LER TEXTOS LONGOS ---
class LorePaginationView(discord.ui.View):
    def __init__(self, title, text):
        super().__init__(timeout=600)
        self.title = title
        # Divide o texto em pedaços de 2000 caracteres (limite do Embed/Msg)
        self.chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
        self.current_page = 0
        self.total_pages = len(self.chunks)

    async def get_page_embed(self):
        embed = discord.Embed(title=f"📖 {self.title}", color=discord.Color.blue())
        embed.description = self.chunks[self.current_page]
        embed.set_footer(text=f"Página {self.current_page + 1}/{self.total_pages} • Total de caracteres: {sum(len(c) for c in self.chunks)}")
        return embed

    async def update_buttons(self, interaction):
        self.children[0].disabled = (self.current_page == 0) # Botão Voltar
        self.children[1].disabled = (self.current_page == self.total_pages - 1) # Botão Próximo
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_buttons(interaction)

    @discord.ui.button(label="Próximo ▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_buttons(interaction)

# --- GOOGLE DRIVE & YT-DLP ---
def get_drive_service():
    scopes = ['https://www.googleapis.com/auth/drive.file']
    creds = None
    if os.path.exists(CREDENTIALS_PATH): 
        creds = Credentials.from_authorized_user_file(CREDENTIALS_PATH, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                log_to_gui(f"ERRO CRÍTICO: '{CLIENT_SECRET_FILE}' não encontrado.", "ERROR"); return None
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes)
            creds = flow.run_local_server(port=0)
        with open(CREDENTIALS_PATH, 'w') as token_file: token_file.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def get_song_info(youtube_url):
    ydl_opts = {'format': 'bestaudio[ext=m4a]/bestaudio/best', 'quiet': True, 'noplaylist': True,'cookies-from-browser': ('firefox',),'force_ipv4': True}
    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(youtube_url, download=False)
            return info.get('title'), info.get('duration'), info.get('filesize_approx'), info.get('uploader')
        except Exception as e:
            log_to_gui(f"Falha ao obter info do vídeo: {e}", "ERROR"); return None, None, None, None

def download_audio_file(youtube_url, temp_dir_path):
    ydl_opts = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': os.path.join(temp_dir_path, '%(id)s.%(ext)s'),'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],'quiet': True, 'noplaylist': True,'cookies-from-browser': ('firefox',),'force_ipv4': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        return base + ".mp3", info.get('title', 'audio')
    
def upload_to_drive(file_path, title):
    if not DRIVE_FOLDER_ID: raise Exception("ID da pasta do Google Drive não configurado.")
    service = get_drive_service()
    if not service: raise Exception("Serviço do Google Drive não autenticado.")
    file_name_on_drive = re.sub(r'[<>:"/\\|?*]', '', f"{title}.mp3")
    metadata = {'name': file_name_on_drive, 'parents': [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype='audio/mpeg', resumable=True)
    file = service.files().create(body=metadata, media_body=media, fields='id').execute()
    fid = file.get('id')
    service.permissions().create(fileId=fid, body={'role':'reader','type':'anyone'}).execute()
    return f"https://drive.google.com/uc?export=download&id={fid}"

async def _perform_song_download_upload_cache(youtube_url: str, initial_title: str, user_name: str, duration_seconds: int):
    log_to_gui(f"Iniciando download de '{initial_title}' para {user_name}.")
    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path, downloaded_title = await asyncio.to_thread(download_audio_file, youtube_url, temp_dir)
        actual_dl_title = downloaded_title or initial_title
        log_to_gui(f"Upload de '{actual_dl_title}' para o Drive iniciado.")
        drive_link = await asyncio.to_thread(upload_to_drive, audio_path, actual_dl_title)
    
    cursor = bot.db_conn.cursor()
    normalized_final_title = normalize_title(actual_dl_title)
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor.execute("INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration, added_by, created_at) VALUES (?,?,?,?,?,?,?)",
                       (youtube_url, drive_link, actual_dl_title, normalized_final_title, duration_seconds, user_name, current_time_str))
    except sqlite3.OperationalError:
        cursor.execute("INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration) VALUES (?,?,?,?,?)",
                       (youtube_url, drive_link, actual_dl_title, normalized_final_title, duration_seconds))

    bot.db_conn.commit()
    log_to_gui(f"'{actual_dl_title}' adicionada com sucesso por {user_name}.", "SUCCESS")
    return actual_dl_title, drive_link

def rebuild_database_from_drive(force=False):
    with db_lock:
        cursor = bot.db_conn.cursor()
    log_to_gui("Iniciando reconstrução de emergência do banco...", "INFO")
    service = get_drive_service()
    if not service: log_to_gui("ERRO: Não foi possível conectar ao Google Drive.", "ERROR"); return
    cursor = bot.db_conn.cursor()
    if force:
        log_to_gui("Limpando banco de dados (force=True)...", "INFO")
        cursor.execute("DELETE FROM music_cache")
        bot.db_conn.commit()
    log_to_gui("Carregando arquivos da pasta do Drive...", "INFO")
    results = service.files().list(q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType='audio/mpeg'", fields="files(id, name)", pageSize=1000).execute()
    files = results.get("files", [])
    for file in files:
        file_id = file["id"]
        name = file["name"]
        youtube_url = f"drive:{file_id}"
        title = name.replace(".mp3", "")
        normalized = normalize_title(title)
        drive_link = f"https://drive.google.com/uc?export=download&id={file_id}"
        cursor.execute("""
            INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (youtube_url, drive_link, title, normalized, 0, "Sistema (REBUILD)", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    bot.db_conn.commit()
    log_to_gui(f"Reconstrução concluída! {len(files)} músicas restauradas.", "SUCCESS")

# --- COMANDOS MÚSICA (SLASH) ---
class AddMusicModal(discord.ui.Modal, title="Adicionar Nova Música"):
    def __init__(self, original_interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.youtube_link = discord.ui.TextInput(label="Link do YouTube", placeholder="https://www.youtube.com/watch?v=...", style=discord.TextStyle.short, required=True)
        self.add_item(self.youtube_link)
    async def on_submit(self, interaction: discord.Interaction):
        link = self.youtube_link.value
        await interaction.response.defer(thinking=True)
        await process_slash_music_addition(interaction, link)

def extract_youtube_id(url):
    patterns = [r"(?:youtu\.be/)([A-Za-z0-9_-]{11})", r"(?:youtube\.com.*[?&]v=)([A-Za-z0-9_-]{11})", r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})"]
    for p in patterns:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def get_best_thumbnail(video_id):
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

def get_thumbnail_dominant_color(url):
    try:
        response = requests.get(url, timeout=5)
        img = Image.open(BytesIO(response.content)).convert("RGB").resize((50, 50))
        pixels = list(img.getdata())
        return discord.Color.from_rgb(*max(set(pixels), key=pixels.count))
    except: return discord.Color.blurple()

async def process_slash_music_addition(interaction: discord.Interaction, youtube_url: str):
    cursor = bot.db_conn.cursor()
    row_url = cursor.execute("SELECT drive_link, title FROM music_cache WHERE youtube_url=?", (youtube_url,)).fetchone()
    if row_url: await interaction.followup.send(f"🔁 A música **{row_url[1]}** já está no Drive:\n{row_url[0]}"); return
    
    title_from_info, duration_sec, filesize_bytes, _ = await asyncio.to_thread(get_song_info, youtube_url)
    if not title_from_info: await interaction.followup.send("❌ Erro ao obter info."); return
    
    try:
        await interaction.edit_original_response(content=f"⏳ *P3LUCHE está analisando as frequências de* **{title_from_info}**...")
        actual_title, drive_link = await _perform_song_download_upload_cache(youtube_url, title_from_info, interaction.user.name, duration_sec)
        
        video_id = extract_youtube_id(youtube_url)
        thumbnail_url = get_best_thumbnail(video_id) if video_id else None
        color = get_thumbnail_dominant_color(thumbnail_url) if thumbnail_url else discord.Color.blurple()

        p3luche_comments = [f"Salvei essa nos meus circuitos! {EMOTE_CANSADO}", "Analisando batidas... *bip boop*... Aprovado! 🎵", f"Essa frequência faz meus sensores superaquecerem! {EMOTE_FOGO}", "Arquivo baixado com sucesso. *P3LUCHE balança a cauda robotica.*"]
        random_comment = random.choice(p3luche_comments)

        embed = discord.Embed(title=f"🎵 {actual_title}", description=f"{interaction.user.mention}\n\n🤖 **P3LUCHE diz:**\n*{random_comment}*\n\n🔗 **Link para o Rádio:**\n{drive_link}", color=color)
        if thumbnail_url: embed.set_thumbnail(url=thumbnail_url)
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ Erro crítico: {str(e)}")
        log_to_gui(str(e), "ERROR")

# --- GRUPO DE COMANDOS DE MÚSICA (ATUALIZADO FASE 2.2) ---
musica_group = app_commands.Group(name="musica", description="Comandos relacionados a músicas.")

@musica_group.command(name="adicionar", description="Adiciona uma nova música.")
async def musica_adicionar(interaction: discord.Interaction, link: str):
    if not await check_channel_permission(interaction): return
    await interaction.response.defer(thinking=True)
    await process_slash_music_addition(interaction, link)

@musica_group.command(name="buscar", description="Busca por uma música ativa.")
async def musica_buscar(interaction: discord.Interaction, termo: str):
    if not await check_channel_permission(interaction): return
    cursor = bot.db_conn.cursor()
    normalized_search = normalize_title(termo)
    
    # ATUALIZAÇÃO FASE 2.2: Adicionado filtro AND is_active = 1
    # Também incluí o ID na resposta para facilitar a edição/remoção
    rows = cursor.execute("""
        SELECT id, title, drive_link 
        FROM music_cache 
        WHERE (normalized_title LIKE ? OR title LIKE ?) AND is_active = 1 
        ORDER BY title COLLATE NOCASE LIMIT 10
    """, (f"%{normalized_search}%", f"%{termo}%")).fetchall()
    
    if rows:
        # Agora mostramos o ID ao lado do título para ajudar os admins
        description = "\n".join([f"**{i+1}. {t}** (ID: {mid})\n   [Link do Drive]({l})" for i, (mid, t, l) in enumerate(rows)])
        embed = discord.Embed(title=f"🎶 Músicas encontradas para '{termo}':", color=discord.Color.green(), description=description)
        embed.set_footer(text="Use o ID mostrado para editar ou ocultar músicas.")
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(f"🔍 Nenhuma música ativa encontrada para '**{termo}**'.", ephemeral=True)

# --- NOVOS COMANDOS DE GOVERNANÇA (FASE 2.2) ---

@musica_group.command(name="editar", description="Renomeia uma música no acervo (Staff).")
@app_commands.describe(id_musica="ID da música (veja no /buscar)", novo_titulo="O novo nome da música")
async def musica_editar(interaction: discord.Interaction, id_musica: int, novo_titulo: str):
    # Verifica Permissão (Apenas Staff)
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Apenas Bibliotecários (Staff) podem renomear faixas.", ephemeral=True)

    cursor = bot.db_conn.cursor()
    song = cursor.execute("SELECT title FROM music_cache WHERE id = ?", (id_musica,)).fetchone()
    
    if not song:
        return await interaction.response.send_message(f"❌ Música com ID **{id_musica}** não encontrada.", ephemeral=True)

    old_title = song['title']
    new_norm = normalize_title(novo_titulo)
    
    cursor.execute("""
        UPDATE music_cache 
        SET title = ?, normalized_title = ?, edited_by = ?, edited_at = ? 
        WHERE id = ?
    """, (novo_titulo, new_norm, interaction.user.name, datetime.now(), id_musica))
    bot.db_conn.commit()
    
    embed = discord.Embed(title="✏️ Música Renomeada", color=discord.Color.gold())
    embed.add_field(name="Antes", value=old_title, inline=True)
    embed.add_field(name="Depois", value=novo_titulo, inline=True)
    embed.set_footer(text=f"Editado por {interaction.user.name}")
    
    await interaction.response.send_message(embed=embed)

@musica_group.command(name="ocultar", description="Oculta uma música da biblioteca (Soft Delete).")
@app_commands.describe(id_musica="ID da música para esconder")
async def musica_ocultar(interaction: discord.Interaction, id_musica: int):
    # Verifica Permissão
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Acesso Negado.", ephemeral=True)

    cursor = bot.db_conn.cursor()
    # Verifica se existe e se já não está oculta
    song = cursor.execute("SELECT title, is_active FROM music_cache WHERE id = ?", (id_musica,)).fetchone()
    
    if not song:
        return await interaction.response.send_message(f"❌ ID **{id_musica}** não encontrado.", ephemeral=True)
    
    if song['is_active'] == 0:
        return await interaction.response.send_message(f"⚠️ A música **'{song['title']}'** já está oculta.", ephemeral=True)

    cursor.execute("""
        UPDATE music_cache 
        SET is_active = 0, edited_by = ?, edited_at = ? 
        WHERE id = ?
    """, (interaction.user.name, datetime.now(), id_musica))
    bot.db_conn.commit()
    
    await interaction.response.send_message(f"🗑️ A música **{song['title']}** foi movida para a lixeira (Oculta).", ephemeral=True)

@musica_group.command(name="restaurar", description="Traz de volta uma música oculta.")
@app_commands.describe(id_musica="ID da música para restaurar")
async def musica_restaurar(interaction: discord.Interaction, id_musica: int):
    # Verifica Permissão
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Acesso Negado.", ephemeral=True)

    cursor = bot.db_conn.cursor()
    song = cursor.execute("SELECT title, is_active FROM music_cache WHERE id = ?", (id_musica,)).fetchone()
    
    if not song:
        return await interaction.response.send_message(f"❌ ID **{id_musica}** não encontrado.", ephemeral=True)
    
    if song['is_active'] == 1:
        return await interaction.response.send_message(f"⚠️ A música **'{song['title']}'** já está ativa.", ephemeral=True)

    cursor.execute("""
        UPDATE music_cache 
        SET is_active = 1, edited_by = ?, edited_at = ? 
        WHERE id = ?
    """, (interaction.user.name, datetime.now(), id_musica))
    bot.db_conn.commit()
    
    await interaction.response.send_message(f"♻️ A música **{song['title']}** foi restaurada com sucesso!", ephemeral=True)

bot.tree.add_command(musica_group)

@bot.tree.command(name="biblioteca", description="Navega pela biblioteca.")
async def biblioteca(interaction: discord.Interaction):
    if not await check_channel_permission(interaction): return
    view = LibrarySortView()
    await interaction.response.send_message("Selecione como deseja organizar a biblioteca:", view=view)

@bot.tree.command(name="stats", description="Mostra estatísticas detalhadas do sistema.")
async def stats(interaction: discord.Interaction):
    # 1. Cálculos de Latência e Uptime
    ping = round(bot.latency * 1000)
    
    uptime_str = "Calculando..."
    if hasattr(bot, 'start_time'):
        uptime = datetime.now() - bot.start_time
        uptime_str = str(uptime).split('.')[0] # Remove milissegundos

    # 2. Dados do Sistema (CPU e RAM)
    cpu_usage = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    ram_used = round(ram.used / 1024**3, 2)  # GB
    ram_total = round(ram.total / 1024**3, 2) # GB
    ram_percent = ram.percent

    # 3. Contagem de Alcance
    server_count = len(bot.guilds)
    member_count = sum(guild.member_count for guild in bot.guilds)

    # CRIANDO O EMBED
    embed = discord.Embed(
        title="📊 Estatísticas do Sistema P3LUCHE",
        color=discord.Color.purple(),
        timestamp=datetime.now()
    )

    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    # Coluna 1: Desempenho
    embed.add_field(name="⚡ Performance", value=f"**Ping:** `{ping}ms`\n**Uptime:** `{uptime_str}`", inline=True)
    
    # Coluna 2: Hardware
    embed.add_field(name="🖥️ Hardware", value=f"**CPU:** `{cpu_usage}%`\n**RAM:** `{ram_used}/{ram_total}GB` ({ram_percent}%)", inline=True)
    
    # Coluna 3: Alcance (Fica na linha de baixo agora para balancear)
    embed.add_field(name="🌐 Alcance", value=f"**Servidores:** `{server_count}`\n**Usuários:** `{member_count}`", inline=False)

    embed.set_footer(text=f"Solicitado por {interaction.user.name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

    await interaction.response.send_message(embed=embed)

# --- COMANDO MENSAGEM MANUAL ---#
@bot.tree.command(name="mensagem_manual", description="Envia uma mensagem manual em um canal específico (Apenas Criador).")
@app_commands.describe(
    canal="O canal onde a mensagem será enviada.",
    mensagem="O conteúdo da mensagem a ser enviada."
)
async def slash_enviar_manual(interaction: discord.Interaction, canal: discord.TextChannel, mensagem: str):
    
    # 1. Verificação de Permissão (Apenas o Criador/Pai pode usar)
    if interaction.user.id != CREATOR_ID:
        await interaction.response.send_message("🚫 Acesso Negado. Apenas o meu criador/pai pode usar este comando. Miau!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        # 2. Envia a mensagem para o canal escolhido
        await canal.send(mensagem)
        
        # 3. Confirmação (apenas para quem enviou)
        await interaction.followup.send(f"✅ Mensagem enviada com sucesso para o canal **#{canal.name}**!", ephemeral=True)

    except discord.Forbidden:
        # O bot não tem permissão para escrever no canal
        await interaction.followup.send(f"❌ Erro: Eu não tenho permissão para enviar mensagens no canal **#{canal.name}**.", ephemeral=True)
    except Exception as e:
        # Outro erro
        await interaction.followup.send(f"❌ Erro ao enviar a mensagem: {e}", ephemeral=True)

@bot.tree.command(name="ajuda", description="Mostra o manual de comandos atualizado do P3LUCHE.")
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Manual de Protocolos P3LUCHE v2.0", 
        description="*Bip boop...* Aqui está a lista de tudo que meus circuitos podem fazer:",
        color=discord.Color.from_rgb(0, 229, 255) # Um azul ciano tecnológico
    )
    
    # --- SEÇÃO DE MÚSICA ---
    music_txt = (
        "`/musica adicionar [link]` - Adiciona música (YouTube) ao Rádio.\n"
        "`/musica buscar [termo]` - Pesquisa músicas já salvas no meu HD.\n"
        "`/biblioteca` - Navega por todas as músicas com filtros.\n"
        "`/stats` - Mostra tempo online e tamanho do cache."
    )
    embed.add_field(name="🎵 Rádio & Música", value=music_txt, inline=False)

    # --- SEÇÃO DE LORE ---
    lore_txt = (
        "`/lore player [user] [nome] [arquivo/texto]` - Arquiva a história de um personagem.\n"
        "`/lore server [arquivo/texto]` - Arquiva a história do mundo/servidor.\n"
        "💡 *Dica: Aceito arquivos PDF, DOCX e TXT. O texto é extraído automaticamente!*"
    )
    embed.add_field(name="📜 Biblioteca de Alexandria (Lore)", value=lore_txt, inline=False)

    # --- SEÇÃO DE MODERAÇÃO ---
    mod_txt = (
        "`/advertencia` - Aplica warn + Nota Fiscal + DM (IA sugere ban no 4º).\n"
        "`/historico [user]` - Vê a ficha criminal completa do player.\n"
        "`/perdoar [id]` - Remove uma advertência pelo ID."
    )
    embed.add_field(name="⚖️ Sistema de Justiça (Staff)", value=mod_txt, inline=False)

    # --- SEÇÃO DE IA ---
    ai_txt = (
        "**Conversa:** Me marque (@P3LUCHE) para conversar.\n"
        "**Dúvidas de Lore:** Pergunte 'Quem é o fulano?' ou 'Me conte sobre o reino X'.\n"
        "**Memória:** Diga 'Lembre-se que [algo]' e eu anoto no meu banco de dados pessoal."
    )
    embed.add_field(name="🧠 Inteligência Artificial", value=ai_txt, inline=False)

    embed.set_footer(text="Sistema Operacional P3LUCHE • Theflerres CoffeeHouse")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- CLASSES DA INTERFACE DO ACERVO (BIBLIOTECA DE ALEXANDRIA) ---

class AskLoreModal(discord.ui.Modal, title="Consultar a Sabedoria Ancestral"):
    def __init__(self, lore_content, model, target_name):
        super().__init__()
        self.lore_content = lore_content
        self.model = model
        self.target_name = target_name
        
        self.question = discord.ui.TextInput(
            label="Qual sua dúvida?",
            placeholder=f"O que deseja saber sobre {target_name}?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            # Prompt focado em responder a pergunta com base na lore
            prompt = (
                f"Você é o Guardião da Biblioteca P3LUCHE. Use APENAS o texto abaixo para responder à pergunta.\n"
                f"TEXTO FONTE ({self.target_name}):\n{self.lore_content[:25000]}\n\n" # Limite de segurança de caracteres
                f"PERGUNTA DO USUÁRIO: {self.question.value}\n\n"
                f"Resposta (seja direto e cite se a informação consta ou não no texto):"
            )
            response = await self.model.generate_content_async(prompt)
            
            embed = discord.Embed(title=f"❓ Pergunta sobre: {self.target_name}", color=discord.Color.gold())
            embed.add_field(name="Dúvida", value=self.question.value, inline=False)
            embed.add_field(name="Resposta do Arquivo", value=response.text[:1024], inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao consultar os astros: {e}")

class AcervoActionsView(discord.ui.View):
    def __init__(self, bot_ref, lore_type, target_id=None, target_name="Mundo"):
        super().__init__(timeout=300)
        self.bot = bot_ref
        self.lore_type = lore_type # 'server' ou 'player'
        self.target_id = target_id
        self.target_name = target_name

    def get_full_lore(self):
        cursor = self.bot.db_conn.cursor()
        if self.lore_type == 'server':
            rows = cursor.execute("SELECT content FROM server_lore ORDER BY created_at ASC").fetchall()
        else:
            rows = cursor.execute("SELECT content FROM player_lore WHERE target_id = ? ORDER BY created_at ASC", (self.target_id,)).fetchall()
        
        if not rows: return ""
        return "\n\n=== REGISTRO ===\n".join([r[0] for r in rows])

    @discord.ui.button(label="Baixar .txt Completo", style=discord.ButtonStyle.secondary, emoji="📄")
    async def download_txt(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text:
            return await interaction.response.send_message("📭 O arquivo está vazio.", ephemeral=True)
        
        file_data = BytesIO(full_text.encode('utf-8'))
        file = discord.File(file_data, filename=f"Lore_{self.target_name.replace(' ', '_')}.txt")
        await interaction.response.send_message(f"📂 Aqui está o arquivo completo de **{self.target_name}**.", file=file, ephemeral=True)

    @discord.ui.button(label="Pedir Resumo (IA)", style=discord.ButtonStyle.primary, emoji="📝")
    async def summarize(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text: return await interaction.response.send_message("📭 Nada para resumir.", ephemeral=True)
        
        # Pega a IA do Cog
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not cog.model: return await interaction.response.send_message("❌ IA offline.", ephemeral=True)

        await interaction.response.defer(thinking=True)
        try:
            prompt = f"Faça um resumo estruturado em tópicos (bullet points) das informações mais importantes deste texto de Lore ({self.target_name}):\n\n{full_text[:30000]}"
            response = await cog.model.generate_content_async(prompt)
            
            embed = discord.Embed(title=f"📝 Resumo: {self.target_name}", description=response.text[:4000], color=discord.Color.blue())
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Erro na IA: {e}")

    @discord.ui.button(label="Fazer Pergunta Específica", style=discord.ButtonStyle.success, emoji="❓")
    async def ask_specific(self, interaction: discord.Interaction, button: discord.ui.Button):
        full_text = self.get_full_lore()
        if not full_text: return await interaction.response.send_message("📭 Nada para consultar.", ephemeral=True)
        
        cog = self.bot.get_cog("P3luchePersona")
        if not cog or not cog.model: return await interaction.response.send_message("❌ IA offline.", ephemeral=True)

        await interaction.response.send_modal(AskLoreModal(full_text, cog.model, self.target_name))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Retornando ao saguão...", embed=get_hub_embed(), view=AcervoHubView(self.bot))

class PlayerSelect(discord.ui.Select):
    def __init__(self, bot_ref, players_data):
        self.bot = bot_ref
        options = []
        # Limita a 25 opções (limite do Discord)
        for p_id, p_name, char_name in players_data[:25]:
            c_name = char_name if char_name else "Desconhecido"
            label = f"{p_name}"
            desc = f"Personagem: {c_name}"
            options.append(discord.SelectOption(label=label, description=desc, value=str(p_id), emoji="👤"))
        
        super().__init__(placeholder="Selecione um Player para acessar a ficha...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        target_id = int(self.values[0])
        # Pega o nome para exibir bonito
        selected_option = [o for o in self.options if o.value == self.values[0]][0]
        target_name = f"{selected_option.label} ({selected_option.description})"
        
        embed = discord.Embed(
            title=f"📂 Arquivo Selecionado: {target_name}",
            description="O que você deseja fazer com os registros deste convidado?",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, 'player', target_id, target_name))

class AcervoHubView(discord.ui.View):
    def __init__(self, bot_ref):
        super().__init__(timeout=None)
        self.bot = bot_ref

    @discord.ui.button(label="Lore do Mundo (Servidor)", style=discord.ButtonStyle.blurple, emoji="🌍")
    async def server_lore_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🌍 Lore Global do Mundo", description="Selecione uma ação para o histórico do servidor.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=AcervoActionsView(self.bot, 'server', target_name="Mundo/Servidor"))

    @discord.ui.button(label="Lore dos Convidados (Players)", style=discord.ButtonStyle.green, emoji="👥")
    async def players_lore_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Busca players no banco
        cursor = self.bot.db_conn.cursor()
        players = cursor.execute("SELECT DISTINCT target_id, target_name, character_name FROM player_lore ORDER BY target_name ASC").fetchall()
        
        if not players:
            return await interaction.response.send_message("📭 Nenhum player registrou lore ainda.", ephemeral=True)

        embed = discord.Embed(title="👥 Arquivo de Convidados", description="Selecione abaixo de qual player você quer ver os registros.", color=discord.Color.green())
        view = discord.ui.View()
        view.add_item(PlayerSelect(self.bot, players))
        # Botão de voltar no menu de players
        back_btn = discord.ui.Button(label="Voltar", style=discord.ButtonStyle.danger, row=1)
        async def back_callback(inter):
            await inter.response.edit_message(embed=get_hub_embed(), view=AcervoHubView(self.bot))
        back_btn.callback = back_callback
        view.add_item(back_btn)
        
        await interaction.response.edit_message(embed=embed, view=view)

def get_hub_embed():
    embed = discord.Embed(
        title="🏛️ Biblioteca de Alexandria - HUB",
        description="Bem-vindo ao acervo central de conhecimento do P3LUCHE.\n\nAqui você tem acesso total aos registros históricos do mundo e dos viajantes (players). Selecione uma categoria abaixo para iniciar.",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3534/3534033.png") # Ícone de livro
    embed.set_footer(text="Acesso Restrito: Nível Administrador")
    return embed

# --- COMANDO /ACERVO ---

@bot.tree.command(name="acervo", description="Abre o HUB da Biblioteca de Alexandria (Apenas Staff).")
async def acervo(interaction: discord.Interaction):
    # Verifica permissão
    has_role = any(role.id in MOD_ROLE_IDS for role in interaction.user.roles)
    if not has_role:
        await interaction.response.send_message("🚫 **Acesso Negado.** Você não tem credenciais de Bibliotecário-Chefe.", ephemeral=True)
        return

    await interaction.response.send_message(embed=get_hub_embed(), view=AcervoHubView(bot))

# --- GRUPO DE COMANDOS /LORE ---
lore_group = app_commands.Group(name="lore", description="Gerenciamento da Biblioteca de Alexandria")

# --- COMANDO 1: ADICIONAR LORE (ATUALIZADO: Players podem adicionar a própria) ---
@lore_group.command(name="player", description="Arquiva a lore de um personagem (Aceita PDF, DOCX, TXT).")
@app_commands.describe(
    usuario="De quem é essa lore? (Se você não for Staff, só pode escolher a si mesmo)", 
    nome_personagem="Nome do Personagem (RP)", 
    arquivo1="Primeiro arquivo (Opcional)", 
    arquivo2="Segundo arquivo (Opcional)", 
    arquivo3="Terceiro arquivo (Opcional)", 
    texto="Texto adicional (opcional)"
)
async def lore_player(interaction: discord.Interaction, usuario: discord.Member, nome_personagem: str, arquivo1: discord.Attachment = None, arquivo2: discord.Attachment = None, arquivo3: discord.Attachment = None, texto: str = None):
    
    # VERIFICAÇÃO DE SEGURANÇA
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    
    # Se NÃO for staff, e tentar adicionar lore para OUTRA pessoa -> Bloqueia
    if not is_staff and usuario.id != interaction.user.id:
        return await interaction.response.send_message("🚫 **Permissão Negada.** Você só pode registrar a história do **seu próprio** personagem.", ephemeral=True)

    await interaction.response.defer(thinking=True) 
    
    final_content = ""
    
    # 1. Processa Múltiplos Arquivos
    anexos = [a for a in [arquivo1, arquivo2, arquivo3] if a is not None]
    
    if anexos:
        for i, anexo in enumerate(anexos):
            extracted = await extract_text_from_attachment(anexo)
            if not extracted:
                final_content += f"\n[AVISO: Não foi possível ler o arquivo {i+1}: {anexo.filename}]\n"
            else:
                final_content += f"\n--- CONTEÚDO DO ARQUIVO {i+1} ({anexo.filename}) ---\n{extracted}\n"
    
    # 2. Processa o Texto (Se tiver)
    if texto:
        final_content += f"\n--- NOTA ADICIONAL ---\n{texto}"

    # 3. Validação
    if not final_content.strip():
        await interaction.followup.send("❌ Você precisa enviar pelo menos um arquivo ou escrever algo no campo texto.", ephemeral=True)
        return

    # 4. Salva no Banco
    try:
        cursor = bot.db_conn.cursor()
        cursor.execute("""
            INSERT INTO player_lore (target_id, target_name, character_name, content, added_by) 
            VALUES (?, ?, ?, ?, ?)
        """, (usuario.id, usuario.name, nome_personagem, final_content, interaction.user.name))
        
        bot.db_conn.commit()
        
        embed = discord.Embed(title="📚 Lore Arquivada!", color=discord.Color.green())
        embed.add_field(name="Personagem", value=nome_personagem, inline=True)
        embed.add_field(name="Player", value=usuario.mention, inline=True)
        embed.add_field(name="Status", value="Registrado com sucesso. Você pode editá-la usando /lore editar.", inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao salvar no banco: {e}")

# --- COMANDO PARA PODER EDITAR AS LORES ---
# --- MODAL DE EDIÇÃO COM VERSIONAMENTO (FASE 2.3) ---
class EditLoreModal(discord.ui.Modal, title="Editar Registro Histórico"):
    def __init__(self, lore_id, current_content, table_name):
        super().__init__()
        self.lore_id = lore_id
        self.table_name = table_name
        
        self.new_content = discord.ui.TextInput(
            label="Novo Conteúdo",
            style=discord.TextStyle.paragraph,
            default=current_content[:3900], # Limite do Discord
            required=True,
            max_length=4000
        )
        self.add_item(self.new_content)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cursor = bot.db_conn.cursor()
            
            # --- NOVA TRAVA DE SEGURANÇA ---
            # Antes de salvar, verifica se a lore pertence a quem está editando (ou se é Staff)
            is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
            
            if 'player' in self.table_name and not is_staff:
                # Busca quem é o dono dessa lore no banco
                owner_check = cursor.execute(f"SELECT target_id FROM {self.table_name} WHERE id = ?", (self.lore_id,)).fetchone()
                
                if not owner_check:
                    return await interaction.followup.send("❌ Lore não encontrada.", ephemeral=True)
                
                # Se o ID do dono não bater com o ID de quem clicou
                if owner_check['target_id'] != interaction.user.id:
                    return await interaction.followup.send("🚫 **Tentativa de violação:** Você não é o dono desta história.", ephemeral=True)
            # -------------------------------

            # 1. BUSCAR O CONTEÚDO ATUAL (Para Backup)
            current_data = cursor.execute(f"SELECT content FROM {self.table_name} WHERE id = ?", (self.lore_id,)).fetchone()
            
            if current_data:
                original_text = current_data['content']
                lore_type_tag = 'player' if 'player' in self.table_name else 'server'
                
                # 2. SALVAR NA TABELA DE VERSÕES (BACKUP)
                cursor.execute("""
                    INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (lore_type_tag, self.lore_id, original_text, interaction.user.name, datetime.now()))

            # 3. ATUALIZAR O CONTEÚDO PRINCIPAL
            cursor.execute(f"UPDATE {self.table_name} SET content = ? WHERE id = ?", (self.new_content.value, self.lore_id))
            bot.db_conn.commit()
            
            await interaction.followup.send(f"✅ Registro **#{self.lore_id}** atualizado! Versão antiga salva no histórico.", ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao atualizar: {e}", ephemeral=True)
            log_to_gui(f"Erro no versionamento: {e}", "ERROR")

class SelectLoreToEdit(discord.ui.View):
    def __init__(self, rows, table_name):
        super().__init__(timeout=60)
        self.rows = rows
        self.table_name = table_name
        
        options = []
        for row in rows[:25]: # Limite do select menu
            l_id, content, target = row
            # Tenta criar um resumo curto para o menu
            snippet = (content[:50] + '...') if len(content) > 50 else content
            label = f"ID {l_id} | {target}"
            options.append(discord.SelectOption(label=label, description=snippet, value=str(l_id)))

        select = discord.ui.Select(placeholder="Selecione qual registro editar...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        lore_id = int(self.values[0]) # Pega o ID selecionado
        cursor = bot.db_conn.cursor()
        row = cursor.execute(f"SELECT content FROM {self.table_name} WHERE id = ?", (lore_id,)).fetchone()
        
        if row:
            content = row['content']
            # TRAVA DE SEGURANÇA: Se for maior que 3800 caracteres, não abre o Modal
            if len(content) > 3800:
                embed = discord.Embed(title="🚨 Arquivo Muito Grande!", color=discord.Color.red())
                embed.description = (
                    f"Este registro tem **{len(content)} caracteres**.\n"
                    "O editor rápido do Discord só suporta até 4000.\n\n"
                    "**Como editar com segurança:**\n"
                    "1. Baixe sua lore atual (use `/lore ler` ou `/acervo`).\n"
                    "2. Edite no Bloco de Notas/Word do seu PC.\n"
                    f"3. Use o comando abaixo para enviar o novo arquivo:\n"
                    f"Command: `/lore atualizar id_lore:{lore_id} arquivo:[Anexe o novo]`"
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Se for pequeno, abre o editor normal
                await interaction.response.send_modal(EditLoreModal(lore_id, content, self.table_name))
        else:
            await interaction.response.send_message("❌ Registro não encontrado.", ephemeral=True)

# --- COMANDO EDITAR (ATUALIZADO: Híbrido Staff/Player) ---
@lore_group.command(name="editar", description="Edita uma lore existente.")
@app_commands.describe(tipo="Tipo de lore", usuario="Filtrar por usuário (Apenas Staff pode usar este filtro)")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Minhas Lores / Player Lore", value="player_lore"),
    app_commands.Choice(name="Server Lore (Apenas Staff)", value="server_lore")
])
async def lore_editar(interaction: discord.Interaction, tipo: app_commands.Choice[str], usuario: discord.Member = None):
    
    # 1. DEFINE QUEM ESTÁ USANDO
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    table = tipo.value

    cursor = bot.db_conn.cursor()

    # BLOQUEIO 1: Players não podem mexer na Lore do Servidor
    if table == "server_lore" and not is_staff:
        return await interaction.response.send_message("🚫 Apenas a Staff pode editar a História do Mundo (Server Lore).", ephemeral=True)

    # LÓGICA DE FILTRAGEM
    if table == "player_lore":
        # Se for Staff: Pode ver a de todo mundo OU filtrar por alguém específico
        if is_staff:
            if usuario:
                query = "SELECT id, content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC"
                params = (usuario.id,)
            else:
                query = "SELECT id, content, character_name FROM player_lore ORDER BY created_at DESC LIMIT 25"
                params = ()
        
        # Se for Player Comum: SÓ VÊ A PRÓPRIA LORE
        else:
            if usuario and usuario.id != interaction.user.id:
                return await interaction.response.send_message("🚫 Você não tem permissão para editar a lore de outros jogadores.", ephemeral=True)
            
            query = "SELECT id, content, character_name FROM player_lore WHERE target_id = ? ORDER BY created_at DESC"
            params = (interaction.user.id,)

    else: # server_lore (Já validamos que é staff lá em cima)
        query = "SELECT id, content, 'Mundo' FROM server_lore ORDER BY created_at DESC LIMIT 25"
        params = ()

    # EXECUTA A BUSCA
    rows = cursor.execute(query, params).fetchall()
    
    if not rows:
        msg = "📭 Nenhuma lore encontrada." if is_staff else "📭 Você ainda não registrou nenhuma lore. Use `/lore player` primeiro!"
        return await interaction.response.send_message(msg, ephemeral=True)

    # MOSTRA O MENU DE SELEÇÃO
    # Reutilizamos a classe SelectLoreToEdit que já existe no seu código
    await interaction.response.send_message("Selecione o registro que deseja modificar:", view=SelectLoreToEdit(rows, table), ephemeral=True)

# --- COMANDO HISTÓRICO (ATUALIZADO: Player vê o próprio histórico) ---
@lore_group.command(name="historico", description="Vê versões antigas de uma lore (Backup).")
@app_commands.describe(id_lore="ID do registro original (Veja no /lore editar ou /acervo)")
async def lore_historico(interaction: discord.Interaction, id_lore: int):
    cursor = bot.db_conn.cursor()
    
    # 1. SEGURANÇA: Verificar de quem é essa Lore
    # Precisamos saber se o usuário tem permissão para ver esse histórico
    lore_info = cursor.execute("SELECT target_id, character_name FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    
    if not lore_info:
        # Tenta ver se é Server Lore (Staff apenas)
        server_check = cursor.execute("SELECT id FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        if server_check:
             if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
                 return await interaction.response.send_message("🚫 Apenas Staff pode ver histórico do servidor.", ephemeral=True)
             target_name = "Mundo/Servidor"
        else:
            return await interaction.response.send_message("❌ Lore não encontrada.", ephemeral=True)
    else:
        target_id, target_name = lore_info
        is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
        
        # Se não for Staff e não for o dono da lore -> BLOQUEIA
        if not is_staff and target_id != interaction.user.id:
            return await interaction.response.send_message("🚫 Você só pode ver o histórico das suas próprias histórias.", ephemeral=True)

    # 2. BUSCA AS VERSÕES
    versions = cursor.execute("""
        SELECT id, content, edited_by, created_at 
        FROM lore_versions 
        WHERE original_lore_id = ? 
        ORDER BY created_at DESC
    """, (id_lore,)).fetchall()

    if not versions:
        await interaction.response.send_message(f"📭 O registro **#{id_lore}** nunca foi editado (Sem histórico).", ephemeral=True)
        return

    embed = discord.Embed(title=f"📜 Arquivo Morto: {target_name}", description=f"Histórico de alterações do Registro #{id_lore}", color=discord.Color.light_grey())
    
    for v in versions:
        # Mostra quem mexeu e quando
        embed.add_field(
            name=f"📅 Versão de {v['created_at']} (ID: {v['id']})",
            value=f"**Editado por:** {v['edited_by']}\nUse `/lore diff id_versao:{v['id']}` para ver o que mudou.",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- COMANDO DIFF (ESTILO GITHUB) ---
@lore_group.command(name="diff", description="Mostra o que foi adicionado (+) e removido (-) entre versões.")
@app_commands.describe(id_versao="O ID da versão antiga (pegue no /lore historico)")
async def lore_diff(interaction: discord.Interaction, id_versao: int):
    cursor = bot.db_conn.cursor()

    # 1. Busca a Versão Antiga (Backup)
    old_version = cursor.execute("SELECT original_lore_id, content, created_at, edited_by FROM lore_versions WHERE id = ?", (id_versao,)).fetchone()
    
    if not old_version:
        return await interaction.response.send_message("❌ Versão de histórico não encontrada.", ephemeral=True)

    lore_id = old_version['original_lore_id']
    old_text = old_version['content']
    old_date = old_version['created_at']

    # 2. SEGURANÇA (Mesma lógica do histórico)
    # Verifica se o usuário tem permissão para ver essa comparação
    lore_info = cursor.execute("SELECT target_id, content FROM player_lore WHERE id = ?", (lore_id,)).fetchone()
    
    if lore_info:
        target_id, current_text = lore_info
        is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
        if not is_staff and target_id != interaction.user.id:
            return await interaction.response.send_message("🚫 Acesso Negado.", ephemeral=True)
    else:
        # Pode ser server lore
        server_info = cursor.execute("SELECT content FROM server_lore WHERE id = ?", (lore_id,)).fetchone()
        if server_info and any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
            current_text = server_info['content']
        else:
            return await interaction.response.send_message("❌ Lore original não encontrada.", ephemeral=True)

    # 3. GERA O DIFF (A Mágica do Python)
    # Quebra os textos em linhas para comparar
    diff = difflib.unified_diff(
        old_text.splitlines(), 
        current_text.splitlines(), 
        fromfile=f'Versão Antiga ({old_date})', 
        tofile='Versão Atual', 
        lineterm=''
    )
    
    # Monta o texto visual para o Discord
    diff_text = "\n".join(list(diff))
    
    if not diff_text:
        return await interaction.response.send_message("🤷 Nenhuma diferença encontrada (Os textos são idênticos).", ephemeral=True)

    # 4. ENVIA COMO CÓDIGO COLORIDO (Markdown 'diff')
    # Se for muito grande, envia arquivo
    if len(diff_text) > 1900:
        file = discord.File(BytesIO(diff_text.encode('utf-8')), filename="mudancas.diff")
        await interaction.response.send_message("📑 As mudanças são muito grandes! Baixe o arquivo para ver:", file=file, ephemeral=True)
    else:
        await interaction.response.send_message(f"📊 **Relatório de Mudanças (Diff):**\n```diff\n{diff_text}\n```", ephemeral=True)

@lore_group.command(name="ler", description="Lê uma lore completa com sistema de páginas (Ideal para textos longos).")
@app_commands.describe(id_lore="ID da Lore (veja no /acervo ou /lore editar)")
async def lore_ler(interaction: discord.Interaction, id_lore: int):
    cursor = bot.db_conn.cursor()
    
    # Busca a lore (Tenta player, se não achar tenta server)
    row = cursor.execute("SELECT character_name, content FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    title = ""
    content = ""

    if row:
        title, content = row['character_name'], row['content']
    else:
        row = cursor.execute("SELECT content FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        if row:
            title, content = "Lore do Mundo", row['content']
        else:
            return await interaction.response.send_message("❌ Lore não encontrada com esse ID.", ephemeral=True)

    # Cria a visualização paginada
    view = LorePaginationView(title, content)
    embed = await view.get_page_embed()
    
    # Se só tiver 1 página, desativa o botão "Próximo" logo de cara
    if view.total_pages <= 1:
        view.children[1].disabled = True
        
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@lore_group.command(name="atualizar", description="Atualiza uma lore existente via arquivo (Use para textos grandes).")
@app_commands.describe(id_lore="ID da Lore para substituir", arquivo="O novo arquivo (PDF/DOCX/TXT)")
async def lore_atualizar(interaction: discord.Interaction, id_lore: int, arquivo: discord.Attachment):
    await interaction.response.defer(thinking=True)
    
    # 1. Verifica Permissão e Existência
    cursor = bot.db_conn.cursor()
    
    # Tenta achar em player_lore
    lore_info = cursor.execute("SELECT target_id, content, character_name FROM player_lore WHERE id = ?", (id_lore,)).fetchone()
    table = "player_lore"
    
    if not lore_info:
        # Tenta server_lore
        lore_info = cursor.execute("SELECT id, content FROM server_lore WHERE id = ?", (id_lore,)).fetchone()
        table = "server_lore"
        if not lore_info:
            return await interaction.followup.send("❌ Lore não encontrada.")
            
    # Checagem de Dono/Staff
    is_staff = any(r.id in MOD_ROLE_IDS for r in interaction.user.roles)
    
    if table == "player_lore":
        target_id = lore_info['target_id']
        if not is_staff and target_id != interaction.user.id:
            return await interaction.followup.send("🚫 Você só pode atualizar suas próprias histórias.")
    else:
        if not is_staff:
            return await interaction.followup.send("🚫 Apenas Staff atualiza lore do servidor.")

    # 2. Processa o Novo Arquivo
    new_text = await extract_text_from_attachment(arquivo)
    if not new_text:
        return await interaction.followup.send("❌ Não consegui ler o arquivo enviado.")

    try:
        # 3. Faz BACKUP da versão antiga (Versionamento)
        original_text = lore_info['content']
        lore_type_tag = 'player' if table == 'player_lore' else 'server'
        
        cursor.execute("""
            INSERT INTO lore_versions (lore_type, original_lore_id, content, edited_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (lore_type_tag, id_lore, original_text, interaction.user.name, datetime.now()))

        # 4. Atualiza com o texto novo
        cursor.execute(f"UPDATE {table} SET content = ?, edited_by = ?, edited_at = ? WHERE id = ?", 
                      (new_text, interaction.user.name, datetime.now(), id_lore))
        
        bot.db_conn.commit()
        
        await interaction.followup.send(f"✅ Registro **#{id_lore}** atualizado com sucesso via arquivo! (Backup salvo).")

    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao atualizar: {e}")

# --- FUNÇÃO AUXILIAR PARA GERAR A IMAGEM (RODA EM THREAD SEPARADA) ---
def _generate_graph_image(nodes, edges, node_colors):
    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from(edges)

    # --- LÓGICA DE DIMENSÃO ULTRAWIDE ---
    node_count = len(nodes)
    
    # Largura Base: 16 | Cresce 0.6 por nó (Fica bem largo)
    width = 16 + (node_count * 0.6)
    
    # Altura Base: 9 | Cresce 0.3 por nó (Cresce devagar na altura)
    height = 9 + (node_count * 0.3)
    
    # Limite de segurança para o Discord não rejeitar (Max 100 polegadas)
    width = min(width, 100)
    height = min(height, 60)

    # Cria a figura com DPI 200 (Alta Resolução para Zoom)
    plt.figure(figsize=(width, height), dpi=150, facecolor='#2f3136')
    
    ax = plt.gca()
    ax.set_facecolor('#2f3136')

    # Física do Grafo (k=2.0 espalha bem os nós horizontalmente)
    # 'iterations=150' dá mais tempo pro algoritmo desenrolar os nós
    pos = nx.spring_layout(G, k=2.5, iterations=150, seed=42)

    # Prepara cores
    colors_mapped = [node_colors.get(node, '#5865F2') for node in G.nodes()]

    # Desenha as conexões (Arestas)
    nx.draw_networkx_edges(
        G, pos, 
        edge_color='#99aab5', 
        width=2, 
        alpha=0.4
    )

    # Desenha as Bolinhas (Nós)
    nx.draw_networkx_nodes(
        G, pos, 
        node_size=5000, # Bolas grandes para caber texto
        node_color=colors_mapped, 
        edgecolors='#ffffff', 
        linewidths=3
    )

    # Formatação dos Nomes
    labels = {}
    for node in G.nodes():
        # Quebra texto se passar de 12 letras
        labels[node] = textwrap.fill(str(node), width=12)

    # Desenha os Nomes
    nx.draw_networkx_labels(
        G, pos, 
        labels=labels,
        font_size=12, # Fonte maior e legível
        font_family='sans-serif', 
        font_color='white', 
        font_weight='bold'
    )

    plt.axis('off')
    
    buffer = BytesIO()
    # bbox_inches='tight' corta as bordas inúteis, mantendo o foco no grafo
    plt.savefig(buffer, format='png', bbox_inches='tight', dpi=150)
    buffer.seek(0)
    plt.close()
    return buffer

# --- COMANDO DE GRAFO VISUAL (100% LOCAL / ZERO TOKEN) ---
@lore_group.command(name="grafo", description="Gera uma teia visual (Players = Azul, Mundo = Roxo).")
async def lore_grafo(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        cursor = bot.db_conn.cursor()
        
        # 1. BUSCA PLAYERS (Azul)
        p_rows = cursor.execute("SELECT character_name, content FROM player_lore WHERE character_name IS NOT NULL").fetchall()
        
        # 2. BUSCA LORE DO SERVIDOR (Roxo)
        s_rows = cursor.execute("SELECT content FROM server_lore").fetchall()

        if len(p_rows) < 1:
            return await interaction.followup.send("❌ Preciso de pelo menos alguns players para desenhar.")

        nodes = []
        edges = []
        node_colors = {} # Dicionário para guardar a cor de cada um

        # --- PROCESSAMENTO DOS PLAYERS (AZUL) ---
        # Dicionário {Nome: Texto}
        player_dict = {row['character_name']: row['content'].lower() for row in p_rows}
        all_players = list(player_dict.keys())

        for p_name in all_players:
            nodes.append(p_name)
            node_colors[p_name] = '#5865F2' # Azul Discord (Player)

        # Conexões Player <-> Player
        for origin in all_players:
            origin_txt = player_dict[origin]
            for target in all_players:
                if origin == target: continue
                if target.lower() in origin_txt:
                    edges.append((origin, target))

        # --- PROCESSAMENTO DO MUNDO (ROXO) ---
        if s_rows:
            server_node_name = "Mundo" # Nome do nó central
            nodes.append(server_node_name)
            node_colors[server_node_name] = '#9b59b6' # Roxo (Server)
            
            # Junta toda a lore do servidor num textão só para analisar
            full_server_lore = " ".join([r['content'].lower() for r in s_rows])

            # Checa: O Mundo cita algum Player?
            for p_name in all_players:
                # Se o nome do player aparece na lore do servidor -> Conecta
                if p_name.lower() in full_server_lore:
                    edges.append((server_node_name, p_name))
                
                # Checa: O Player cita o Mundo? (palavras chave)
                # Se o player escreveu "o reino", "o mundo", "o servidor" -> Conecta
                player_txt = player_dict[p_name]
                if any(x in player_txt for x in ["mundo", "reino", "servidor", "capital", "história"]):
                    edges.append((p_name, server_node_name))

        if not edges:
            return await interaction.followup.send("❌ Não encontrei conexões suficientes para desenhar.")

        # Remove duplicatas nas conexões (A->B é igual a B->A para grafos simples)
        edges = list(set(tuple(sorted(e)) for e in edges))

        # 3. GERA A IMAGEM (Passando as cores agora)
        image_buffer = await asyncio.to_thread(_generate_graph_image, nodes, edges, node_colors)

        file = discord.File(image_buffer, filename="teia_destinos.png")
        embed = discord.Embed(
            title="🕸️ Teia de Destinos", 
            description="🔵 **Azul:** Players\n🟣 **Roxo:** História do Mundo/Servidor",
            color=discord.Color.blurple()
        )
        embed.set_image(url="attachment://teia_destinos.png")
        
        await interaction.followup.send(embed=embed, file=file)

    except Exception as e:
        log_to_gui(f"Erro no grafo: {e}", "ERROR")
        await interaction.followup.send(f"❌ Erro ao desenhar: {e}")

# COMANDO ADICIONAR LORE DO SERVIDOR (Só Staff)
@lore_group.command(name="server", description="Arquiva lore do mundo/servidor (Restrito a Staff).")
async def lore_server(interaction: discord.Interaction, arquivo: discord.Attachment = None, texto: str = None):
    # Verifica Permissão
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Apenas a Staff pode escrever a história do mundo.", ephemeral=True)

    await interaction.response.defer(thinking=True)
    final_content = ""
    
    if arquivo:
        extracted = await extract_text_from_attachment(arquivo)
        final_content += f"\n{extracted}\n"
    if texto:
        final_content += f"\n{texto}"

    if not final_content.strip():
        return await interaction.followup.send("❌ Nada para salvar.", ephemeral=True)

    try:
        cursor = bot.db_conn.cursor()
        cursor.execute("INSERT INTO server_lore (content) VALUES (?)", (final_content,))
        bot.db_conn.commit()
        await interaction.followup.send("✅ **Lore Global** adicionada à Biblioteca de Alexandria.")
    except Exception as e:
        await interaction.followup.send(f"Erro: {e}")

bot.tree.add_command(lore_group)

# --- SETUP DE BANCO E STARTUP ---
# --- CLASSE DE GERENCIAMENTO DE BANCO DE DADOS (NOVA) ---
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # Permite acessar colunas pelo nome (ex: row['id']) em vez de índice (row[0])
        self.conn.row_factory = sqlite3.Row 
        return self.conn

    def _add_column_safe(self, table, column_def):
        """Tenta adicionar uma coluna, ignora se ela já existir."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            print(f"✅ Schema atualizado: Tabela '{table}' recebeu '{column_def}'")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                pass # Coluna já existe, tudo certo
            else:
                print(f"❌ Erro ao alterar schema de {table}: {e}")

    def migrate(self):
        """Executa a criação de tabelas e atualizações de schema (Roadmap Completo + Correção)"""
        cursor = self.conn.cursor()
        
        # 1. CRIAÇÃO DAS TABELAS BASE
        cursor.execute("""CREATE TABLE IF NOT EXISTS music_cache (id INTEGER PRIMARY KEY, youtube_url TEXT UNIQUE, drive_link TEXT, title TEXT, normalized_title TEXT, duration INTEGER, added_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_memories (id INTEGER PRIMARY KEY, user_id INTEGER, user_name TEXT, memory_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS server_lore (id INTEGER PRIMARY KEY, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS player_lore (id INTEGER PRIMARY KEY, target_id INTEGER, target_name TEXT, character_name TEXT, content TEXT, added_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY, user_id INTEGER, user_name TEXT, moderator_id INTEGER, moderator_name TEXT, reason TEXT, proof TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # 2. TABELA: LORE VERSIONS
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lore_versions (
            id INTEGER PRIMARY KEY,
            lore_type TEXT,
            original_lore_id INTEGER,
            content TEXT,
            edited_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 3. TABELA: CACHE DO GRAFO
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lore_graph_cache (
            id INTEGER PRIMARY KEY CHECK (id = 1), 
            mermaid_code TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        self.conn.commit()

        # 4. UPGRADES DE SCHEMA (ADICIONAR COLUNAS QUE FALTAM)
        
        # WARNINGS
        self._add_column_safe("warnings", "status TEXT DEFAULT 'active'")
        self._add_column_safe("warnings", "revoked_by TEXT")
        self._add_column_safe("warnings", "revoked_at TIMESTAMP")

        # MUSIC_CACHE
        self._add_column_safe("music_cache", "is_active INTEGER DEFAULT 1")
        self._add_column_safe("music_cache", "edited_by TEXT")
        self._add_column_safe("music_cache", "edited_at TIMESTAMP")

        # USER_MEMORIES
        self._add_column_safe("user_memories", "tag TEXT")
        self._add_column_safe("user_memories", "importance INTEGER DEFAULT 1")
        self._add_column_safe("user_memories", "is_active INTEGER DEFAULT 1")
        
        # --- AQUI ESTÁ A CORREÇÃO DO ERRO ---
        # Adicionando colunas de edição nas Lores que estavam faltando
        self._add_column_safe("player_lore", "character_name TEXT")
        self._add_column_safe("player_lore", "edited_at TIMESTAMP") # <--- Faltava isso!
        self._add_column_safe("player_lore", "edited_by TEXT")
        
        self._add_column_safe("server_lore", "edited_at TIMESTAMP")
        self._add_column_safe("server_lore", "edited_by TEXT")

        self.conn.commit()
        print("💾 Banco de dados verificado e corrigido (Colunas 'edited_at' adicionadas).")

# Instância global (será iniciada no setup_hook)
db_manager = DatabaseManager(DB_PATH)

# --- SETUP DE BANCO E STARTUP (ESTA PARTE É OBRIGATÓRIA) ---
async def setup_hook():
    # 1. Conecta ao banco usando a nova classe
    bot.db_conn = db_manager.connect()
    
    # 2. Roda as migrações (Cria tabelas e colunas novas)
    db_manager.migrate()
    
    # 3. Carrega a IA e avisa
    await bot.add_cog(P3luchePersona(bot))
    log_to_gui("Sistemas Carregados e Banco Migrado com Sucesso.", "SUCCESS")

# Vincula a função ao bot
bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    bot.start_time = datetime.now()
    log_to_gui(f"Bot Online: {bot.user}", "SUCCESS")
    await bot.tree.sync()
    _populate_normalized_titles_if_empty()
    try:
        cursor = bot.db_conn.cursor()
        if cursor.execute("SELECT COUNT(*) FROM music_cache").fetchone()[0] == 0:
            threading.Thread(target=lambda: asyncio.run(rebuild_database_from_drive(True))).start()
    except: pass

# --- GRUPO DE COMANDOS DE INTELIGÊNCIA ARTIFICIAL (FASE 3) ---
ia_group = app_commands.Group(name="ia", description="Configurações da mente do P3LUCHE.")

@ia_group.command(name="memoria_ver", description="Mostra tudo o que eu lembro sobre você.")
async def ia_memoria_ver(interaction: discord.Interaction):
    cursor = bot.db_conn.cursor()
    # Seleciona apenas memórias ATIVAS (is_active = 1)
    rows = cursor.execute("""
        SELECT id, memory_text, created_at 
        FROM user_memories 
        WHERE user_id = ? AND is_active = 1 
        ORDER BY created_at DESC
    """, (interaction.user.id,)).fetchall()

    if not rows:
        return await interaction.response.send_message("🧠 Minha mente está vazia em relação a você. (Nenhuma memória salva)", ephemeral=True)

    embed = discord.Embed(title=f"🧠 Memórias de {interaction.user.name}", color=discord.Color.magenta())
    embed.set_footer(text="Use /ia memoria_esquecer [ID] para apagar algo.")

    desc = ""
    for row in rows:
        # Acessa por nome graças ao row_factory
        desc += f"🆔 **{row['id']}** | 📅 {row['created_at']}\n📝 *{row['memory_text']}*\n\n"
    
    # Se ficar muito grande, corta (refinamento básico)
    if len(desc) > 4000: desc = desc[:4000] + "... (lista cortada)"
    embed.description = desc
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@ia_group.command(name="memoria_esquecer", description="Apaga uma memória específica pelo ID.")
@app_commands.describe(id_memoria="O ID da memória para apagar")
async def ia_memoria_esquecer(interaction: discord.Interaction, id_memoria: int):
    cursor = bot.db_conn.cursor()
    
    # Verifica se a memória pertence ao usuário (Segurança)
    mem = cursor.execute("SELECT user_id, is_active FROM user_memories WHERE id = ?", (id_memoria,)).fetchone()
    
    if not mem:
        return await interaction.response.send_message("❌ Memória não encontrada.", ephemeral=True)
    
    if mem['user_id'] != interaction.user.id:
        return await interaction.response.send_message("🚫 Você não pode apagar memórias de outras pessoas!", ephemeral=True)

    if mem['is_active'] == 0:
        return await interaction.response.send_message("⚠️ Essa memória já foi apagada.", ephemeral=True)

    # Soft Delete
    cursor.execute("UPDATE user_memories SET is_active = 0 WHERE id = ?", (id_memoria,))
    bot.db_conn.commit()

    await interaction.response.send_message(f"🗑️ Memória **{id_memoria}** removida dos meus circuitos.", ephemeral=True)

bot.tree.add_command(ia_group)

@bot.event
async def on_shutdown():
    if hasattr(bot, 'db_conn'): bot.db_conn.close()

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Erro fatal ao iniciar: {e}")
#FCN 2060???       