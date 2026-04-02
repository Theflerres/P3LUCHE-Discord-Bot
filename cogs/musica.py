"""
Música — Google Drive, yt-dlp, biblioteca e fila lógica de ingestão (YouTube / arquivo).
"""
import asyncio
import os
import random
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from yt_dlp import YoutubeDL

from config import (
    CLIENT_SECRET_FILE,
    CREDENTIALS_PATH,
    DRIVE_FOLDER_ID,
    EMOTE_CANSADO,
    EMOTE_FOGO,
    MAX_SONG_SIZE_MB,
    MOD_ROLE_IDS,
    USER_MUSIC_CHANNEL_ID,
    db_lock,
    get_bot_instance,
    set_bot_instance,
)
from utils import (
    COOKIE_FILE,
    extract_youtube_id,
    format_timedelta,
    get_best_thumbnail,
    get_thumbnail_dominant_color,
    log_to_gui,
    normalize_title,
)

#region SISTEMA_MUSICA

#region HELPERS_API

# --- FUNÇÕES E COMANDOS DE MÚSICA  ---
def _populate_normalized_titles_if_empty():
    cursor = get_bot_instance().db_conn.cursor()
    all_songs = cursor.execute("SELECT id, title FROM music_cache WHERE normalized_title IS NULL OR normalized_title = ''").fetchall()
    if not all_songs: return
    count = 0
    for song_id, song_title in all_songs:
        if song_title:
            norm_title = normalize_title(song_title)
            cursor.execute("UPDATE music_cache SET normalized_title = ? WHERE id = ?", (norm_title, song_id))
            count += 1
    if count > 0: get_bot_instance().db_conn.commit()
    log_to_gui(f"Normalização de {count} títulos concluída.")

async def check_channel_permission(interaction: discord.Interaction) -> bool:
    if isinstance(interaction.channel, discord.DMChannel): return True
    if not USER_MUSIC_CHANNEL_ID: return True
    if interaction.channel.id in USER_MUSIC_CHANNEL_ID: return True
    allowed_mentions = " ".join([f"<#{cid}>" for cid in USER_MUSIC_CHANNEL_ID])
    msg = f"🚫 **Comando não permitido aqui!** Use: {allowed_mentions}"
    if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
    else: await interaction.response.send_message(msg, ephemeral=True)
    return False

#endregion HELPERS_API

#region VIEWS_BIBLIOTECA

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
        
        cursor = get_bot_instance().db_conn.cursor()
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

#endregion VIEWS_BIBLIOTECA

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
    """Obtém informações do vídeo usando o cliente Android para evitar erro 403/SABR."""
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'quiet': True,
        'noplaylist': True,
        'force_ipv4': True,
        'cookiefile': COOKIE_FILE, # <--- Usa seus cookies do Brave [cite: 82]
        'user_agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        'extractor_args': {
            'youtube': {
                # Força o uso do cliente Android, que conforme o log do GitHub, não usa SABR [cite: 84]
                'player_client': ['android', 'ios'],
                # Ignora a necessidade de PO Token para formatos básicos
                'formats': 'missing_pot' 
            }
        }
    }
    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(youtube_url, download=False)
            return info.get('title'), info.get('duration'), info.get('filesize_approx'), info.get('uploader')
        except Exception as e:
            log_to_gui(f"Falha ao obter info do vídeo: {e}", "ERROR")
            return None, None, None, None

def download_audio_file(youtube_url, temp_dir_path):
    """Realiza o download forçando o protocolo HTTPS legado via cliente Android."""
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': os.path.join(temp_dir_path, '%(id)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        'quiet': True,
        'noplaylist': True,
        'force_ipv4': True,
        'cookiefile': COOKIE_FILE, # <--- Seus cookies 
        'extractor_args': {
            'youtube': {
                'player_client': ['android'], # Cliente Android é o mais estável contra SABR atualmente 
                'formats': 'missing_pot'
            }
        }
    }
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
    
    cursor = get_bot_instance().db_conn.cursor()
    normalized_final_title = normalize_title(actual_dl_title)
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor.execute("INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration, added_by, created_at) VALUES (?,?,?,?,?,?,?)",
                       (youtube_url, drive_link, actual_dl_title, normalized_final_title, duration_seconds, user_name, current_time_str))
    except sqlite3.OperationalError:
        cursor.execute("INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration) VALUES (?,?,?,?,?)",
                       (youtube_url, drive_link, actual_dl_title, normalized_final_title, duration_seconds))

    get_bot_instance().db_conn.commit()
    log_to_gui(f"'{actual_dl_title}' adicionada com sucesso por {user_name}.", "SUCCESS")
    return actual_dl_title, drive_link

def rebuild_database_from_drive(force=False):
    with db_lock:
        cursor = get_bot_instance().db_conn.cursor()
    log_to_gui("Iniciando reconstrução de emergência do banco...", "INFO")
    service = get_drive_service()
    if not service: log_to_gui("ERRO: Não foi possível conectar ao Google Drive.", "ERROR"); return
    cursor = get_bot_instance().db_conn.cursor()
    if force:
        log_to_gui("Limpando banco de dados (force=True)...", "INFO")
        cursor.execute("DELETE FROM music_cache")
        get_bot_instance().db_conn.commit()
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
    get_bot_instance().db_conn.commit()
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

async def process_slash_music_addition(interaction: discord.Interaction, youtube_url: str):
    cursor = get_bot_instance().db_conn.cursor()
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

async def process_file_music_addition(interaction: discord.Interaction, attachment: discord.Attachment, title_manual: str):
    # 1. Feedback inicial
    await interaction.edit_original_response(content=f"📥 Baixando **{title_manual}** para o servidor...")

    temp_path = None
    try:
        # 2. Salva o anexo num arquivo temporário
        suffix = os.path.splitext(attachment.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            await attachment.save(tmp.name)
            temp_path = tmp.name

        # 3. Upload para o Google Drive (Reutiliza sua função existente)
        await interaction.edit_original_response(content=f"☁️ Enviando **{title_manual}** para o Google Drive...")
        
        # A função upload_to_drive roda em thread para não travar o bot
        drive_link = await asyncio.to_thread(upload_to_drive, temp_path, title_manual)

        # 4. Salvar no Banco de Dados
        cursor = get_bot_instance().db_conn.cursor()
        
        # Tratamento de Strings
        normalized = normalize_title(title_manual)
        # Gera um ID falso único para a coluna youtube_url já que não é YouTube
        fake_url = f"file_upload:{attachment.id}" 
        duration_mock = 0 # Arquivos diretos não temos a duração fácil sem baixar libs pesadas, deixamos 0 ou estimado

        cursor.execute("""
            INSERT INTO music_cache (youtube_url, drive_link, title, normalized_title, duration, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fake_url, drive_link, title_manual, normalized, duration_mock, interaction.user.name, datetime.now()))
        
        get_bot_instance().db_conn.commit()

        # 5. Mensagem de Sucesso
        embed = discord.Embed(title=f"💿 Arquivo Local Adicionado", color=discord.Color.green())
        embed.description = f"**{title_manual}**\n\n🔗 [Link do Drive]({drive_link})"
        embed.set_footer(text=f"Enviado por {interaction.user.name}")
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2829/2829821.png") # Ícone de arquivo

        await interaction.edit_original_response(content=None, embed=embed)
        log_to_gui(f"Arquivo '{title_manual}' adicionado por {interaction.user.name}.", "SUCCESS")

    except Exception as e:
        log_to_gui(f"Erro ao processar arquivo: {e}", "ERROR")
        await interaction.edit_original_response(content=f"❌ Erro ao processar arquivo: {e}")
    
    finally:
        # Limpeza do arquivo temporário no PC do bot
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

# --- GRUPO DE COMANDOS DE MÚSICA (ATUALIZADO FASE 2.2) ---
musica_group = app_commands.Group(name="musica", description="Comandos relacionados a músicas.")

@musica_group.command(name="adicionar", description="Adiciona música via Link (YouTube) ou Arquivo Direto.")
@app_commands.describe(
    link="Link do YouTube (Deixe vazio se for enviar arquivo)",
    arquivo="Arquivo de áudio (MP3/M4A). Requer 'titulo_manual'!",
    titulo_manual="Nome da música (OBRIGATÓRIO se enviar arquivo)"
)
async def musica_adicionar(interaction: discord.Interaction, link: str = None, arquivo: discord.Attachment = None, titulo_manual: str = None):
    if not await check_channel_permission(interaction): return

    # Validação 1: Nem link nem arquivo
    if not link and not arquivo:
        return await interaction.response.send_message("❌ Você precisa enviar um **Link** OU um **Arquivo**.", ephemeral=True)

    # Validação 2: Arquivo sem título manual
    if arquivo and not titulo_manual:
        return await interaction.response.send_message("❌ Para enviar arquivos, você **DEVE** preencher o campo `titulo_manual` com o nome da música.", ephemeral=True)

    await interaction.response.defer(thinking=True)

    try:
        # ROTA A: Link do YouTube (Lógica Antiga)
        if link:
            await process_slash_music_addition(interaction, link)
        
        # ROTA B: Arquivo Local (Nova Lógica)
        elif arquivo:
            # Verificação de formato e tamanho
            if not arquivo.filename.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg')):
                return await interaction.followup.send("❌ Formato inválido. Use MP3, M4A, WAV ou OGG.")
            
            if arquivo.size > MAX_SONG_SIZE_MB * 1024 * 1024:
                return await interaction.followup.send(f"❌ Arquivo muito grande! Máximo: {MAX_SONG_SIZE_MB}MB.")

            await process_file_music_addition(interaction, arquivo, titulo_manual)

    except Exception as e:
        log_to_gui(f"Erro no comando adicionar: {e}", "ERROR")
        await interaction.followup.send(f"❌ Erro inesperado: {e}")

@musica_group.command(name="buscar", description="Busca por uma música ativa.")
async def musica_buscar(interaction: discord.Interaction, termo: str):
    if not await check_channel_permission(interaction): return
    cursor = get_bot_instance().db_conn.cursor()
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

    cursor = get_bot_instance().db_conn.cursor()
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
    get_bot_instance().db_conn.commit()
    
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

    cursor = get_bot_instance().db_conn.cursor()
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
    get_bot_instance().db_conn.commit()
    
    await interaction.response.send_message(f"🗑️ A música **{song['title']}** foi movida para a lixeira (Oculta).", ephemeral=True)

@musica_group.command(name="restaurar", description="Traz de volta uma música oculta.")
@app_commands.describe(id_musica="ID da música para restaurar")
async def musica_restaurar(interaction: discord.Interaction, id_musica: int):
    # Verifica Permissão
    if not any(r.id in MOD_ROLE_IDS for r in interaction.user.roles):
        return await interaction.response.send_message("🚫 Acesso Negado.", ephemeral=True)

    cursor = get_bot_instance().db_conn.cursor()
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
    get_bot_instance().db_conn.commit()
    
    await interaction.response.send_message(f"♻️ A música **{song['title']}** foi restaurada com sucesso!", ephemeral=True)

@app_commands.command(name="biblioteca", description="Navega pela biblioteca.")
async def biblioteca(interaction: discord.Interaction):
    if not await check_channel_permission(interaction): return
    view = LibrarySortView()
    await interaction.response.send_message("Selecione como deseja organizar a biblioteca:", view=view)


class MusicaCog(commands.Cog):
    """Registra /musica e /biblioteca."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        self.bot.tree.add_command(musica_group)
        self.bot.tree.add_command(biblioteca)


async def setup(bot):
    await bot.add_cog(MusicaCog(bot))
