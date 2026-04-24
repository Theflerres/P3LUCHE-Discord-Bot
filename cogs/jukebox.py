"""
Módulo de música v2 para P3LUCHE.(mudei pra jukebox pra n confundir com o musica.py que ja tem no bot, mas é um módulo completamente novo)

Implementa:
- Reprodução a partir do cache no banco (Google Drive URL)
- Reprodução por URL externa (yt-dlp)
- Fila por guild com avanço automático
- Controles de playback por slash commands
- Helpers de normalização e rebuild de banco a partir do Drive
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from yt_dlp import YoutubeDL

from config import DRIVE_FOLDER_ID, get_bot_instance, set_bot_instance
from utils import log_to_gui


# Cores padronizadas para embeds
COLOR_SUCCESS = discord.Color.green()
COLOR_ERROR = discord.Color.red()
COLOR_INFO = discord.Color.blue()
COLOR_WAITING = discord.Color.yellow()

GENERIC_THUMBNAIL = "https://cdn-icons-png.flaticon.com/512/727/727240.png"


def normalize(text: str) -> str:
    """Remove acentos, baixa caixa e caracteres especiais para busca."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _seconds_to_text(seconds: Optional[int]) -> str:
    if not seconds:
        return "desconhecida"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _music_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_thumbnail(url=GENERIC_THUMBNAIL)
    return embed


def _get_db_conn() -> sqlite3.Connection:
    bot = get_bot_instance()
    if bot is None or not hasattr(bot, "db_conn"):
        raise RuntimeError("Instância do bot/db_conn não disponível.")
    return bot.db_conn


def _get_drive_service():
    """Cria cliente do Google Drive via Service Account em env."""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON não definido.")

    try:
        info = json.loads(creds_json)
    except json.JSONDecodeError:
        if os.path.exists(creds_json):
            with open(creds_json, "r", encoding="utf-8") as file:
                info = json.load(file)
        else:
            raise RuntimeError("GOOGLE_CREDENTIALS_JSON inválido (JSON/path).")

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=credentials)


def _db_fetch_music_candidates(normalized_query: str, limit: int = 5) -> list[sqlite3.Row | tuple]:
    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, normalized_title, drive_file_id, drive_url, duration
        FROM music_cache
        WHERE normalized_title LIKE ?
        ORDER BY title COLLATE NOCASE
        LIMIT ?
        """,
        (f"%{normalized_query}%", limit),
    )
    return cur.fetchall()


def _db_insert_music_cache(
    title: str,
    normalized_title: str,
    drive_file_id: str,
    drive_url: str,
    duration: int,
):
    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO music_cache
        (title, normalized_title, drive_file_id, drive_url, duration, added_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, normalized_title, drive_file_id, drive_url, int(duration or 0), datetime.utcnow().isoformat()),
    )
    conn.commit()


def _db_update_missing_normalized_titles() -> int:
    conn = _get_db_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, title FROM music_cache WHERE normalized_title IS NULL OR normalized_title = ''"
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        song_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        title = row["title"] if isinstance(row, sqlite3.Row) else row[1]
        if not title:
            continue
        cur.execute("UPDATE music_cache SET normalized_title = ? WHERE id = ?", (normalize(title), song_id))
        updated += 1
    conn.commit()
    return updated


def _drive_list_audio_files() -> list[dict[str, str]]:
    service = _get_drive_service()
    query = f"'{DRIVE_FOLDER_ID}' in parents and mimeType contains 'audio/' and trashed = false"
    response = (
        service.files()
        .list(q=query, fields="files(id, name, webContentLink)", pageSize=1000)
        .execute()
    )
    return response.get("files", [])


def _drive_upload_audio(local_path: str, title: str) -> tuple[str, str]:
    service = _get_drive_service()
    safe_name = re.sub(r'[<>:"/\\|?*]', "", title).strip() or "audio"
    metadata = {"name": f"{safe_name}.mp3", "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(local_path, mimetype="audio/mpeg", resumable=True)
    created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    file_id = created["id"]
    service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    drive_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    return file_id, drive_url


def _extract_stream_info(url: str) -> dict[str, Any]:
    opts = {"quiet": True, "noplaylist": True, "format": "bestaudio/best"}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "Sem título",
        "duration": int(info.get("duration") or 0),
        "stream_url": info.get("url"),
        "webpage_url": info.get("webpage_url") or url,
    }


def _download_audio_to_mp3(url: str) -> tuple[str, str, int]:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "%(id)s.%(ext)s")
        opts = {
            "quiet": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": out,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            original = ydl.prepare_filename(info)
            base, _ = os.path.splitext(original)
            mp3_path = f"{base}.mp3"
            title = info.get("title") or "Sem título"
            duration = int(info.get("duration") or 0)
            with open(mp3_path, "rb") as f:
                data = f.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp:
        temp.write(data)
        temp.flush()
        final_path = temp.name
    return final_path, title, duration


@dataclass
class QueueItem:
    title: str
    source_url: str
    duration: int = 0
    requested_by: str = ""
    origin: str = "db"


class TrackChoiceView(discord.ui.View):
    """View para escolher uma faixa quando busca retorna múltiplos resultados."""

    def __init__(self, items: list[dict[str, Any]], requester_id: int):
        super().__init__(timeout=30)
        self.items = items
        self.requester_id = requester_id
        self.selected: Optional[dict[str, Any]] = None
        self._done = asyncio.Event()

        options = []
        for idx, item in enumerate(items[:5], start=1):
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {item['title'][:90]}",
                    description=f"Duração: {_seconds_to_text(item.get('duration', 0))}",
                    value=str(idx - 1),
                )
            )
        self.select.options = options

    @discord.ui.select(placeholder="Escolha uma música...", min_values=1, max_values=1, options=[])
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=_music_embed("Ação não permitida", "Somente quem executou o comando pode escolher.", COLOR_ERROR),
                ephemeral=True,
            )
            return
        self.selected = self.items[int(select.values[0])]
        self._done.set()
        await interaction.response.edit_message(view=None)

    async def wait_for_selection(self) -> Optional[dict[str, Any]]:
        await self.wait()
        if self._done.is_set():
            return self.selected
        return None


class SaveToDriveView(discord.ui.View):
    """View para confirmar persistência no Drive + banco após /tocar_url."""

    def __init__(self, cog: "MusicaV2", url: str, info: dict[str, Any], requester_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.url = url
        self.info = info
        self.requester_id = requester_id

    async def _guard_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=_music_embed("Ação não permitida", "Somente quem executou o comando pode confirmar.", COLOR_ERROR),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Salvar no Drive", style=discord.ButtonStyle.success)
    async def save_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard_user(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await self.cog.save_external_track(self.url, self.info)
            await interaction.followup.send(
                embed=_music_embed("Salvo com sucesso", "A música foi enviada ao Drive e registrada no cache.", COLOR_SUCCESS),
                ephemeral=True,
            )
        except Exception as exc:
            log_to_gui(f"Erro ao salvar faixa externa: {exc}", "ERROR")
            await interaction.followup.send(
                embed=_music_embed("Falha ao salvar", f"Não foi possível persistir: `{exc}`", COLOR_ERROR),
                ephemeral=True,
            )
        finally:
            self.stop()

    @discord.ui.button(label="Agora não", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard_user(interaction):
            return
        await interaction.response.edit_message(view=None)
        self.stop()


class QueuePaginationView(discord.ui.View):
    def __init__(self, entries: list[QueueItem]):
        super().__init__(timeout=60)
        self.entries = entries
        self.page = 0
        self.per_page = 8

    def _max_pages(self) -> int:
        return max(1, (len(self.entries) + self.per_page - 1) // self.per_page)

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.entries[start:end]
        if not chunk:
            desc = "A fila está vazia."
        else:
            lines = []
            for i, item in enumerate(chunk, start=start + 1):
                lines.append(
                    f"**{i}.** {item.title} `({_seconds_to_text(item.duration)})` • pedido por `{item.requested_by or 'desconhecido'}`"
                )
            desc = "\n".join(lines)
        embed = _music_embed("Fila de reprodução", desc, COLOR_INFO)
        embed.set_footer(text=f"Página {self.page + 1}/{self._max_pages()}")
        return embed

    async def _refresh(self, interaction: discord.Interaction):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self._max_pages() - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.blurple)
    async def prev_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    @discord.ui.button(label="Próxima", style=discord.ButtonStyle.blurple)
    async def next_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = min(self._max_pages() - 1, self.page + 1)
        await self._refresh(interaction)


class MusicaV2(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, list[QueueItem]] = {}
        self.voice_clients: dict[int, discord.VoiceClient] = {}
        self.current_tracks: dict[int, QueueItem] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._alone_tasks: dict[int, asyncio.Task] = {}
        set_bot_instance(bot)

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _cleanup_guild_state(self, guild_id: int):
        """Limpa todo o estado em memória associado ao guild."""
        self.queues.pop(guild_id, None)
        self.voice_clients.pop(guild_id, None)
        self.current_tracks.pop(guild_id, None)
        task = self._alone_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _count_non_bot_members(self, voice: Optional[discord.VoiceClient]) -> int:
        if not voice or not voice.channel:
            return 0
        return sum(1 for m in voice.channel.members if not m.bot)

    def _cancel_alone_timer(self, guild_id: int):
        task = self._alone_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_alone_timer(self, guild_id: int):
        self._cancel_alone_timer(guild_id)

        async def _disconnect_if_still_alone():
            try:
                await asyncio.sleep(120)
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    self._cleanup_guild_state(guild_id)
                    return
                voice = guild.voice_client
                if not voice:
                    self._cleanup_guild_state(guild_id)
                    return
                if self._count_non_bot_members(voice) == 0:
                    try:
                        voice.stop()
                    except Exception:
                        pass
                    await voice.disconnect(force=True)
                    self._cleanup_guild_state(guild_id)
                    log_to_gui(f"Desconectado por inatividade (sozinho) na guild {guild_id}.", "INFO")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log_to_gui(f"Erro no timer de inatividade da guild {guild_id}: {exc}", "ERROR")

        self._alone_tasks[guild_id] = self.bot.loop.create_task(_disconnect_if_still_alone())

    async def _ensure_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        if not interaction.guild:
            return None
        if not interaction.user or not getattr(interaction.user, "voice", None):
            await interaction.followup.send(
                embed=_music_embed("Canal de voz obrigatório", "Entre em um canal de voz para usar este comando.", COLOR_ERROR),
                ephemeral=True,
            )
            return None

        channel = interaction.user.voice.channel
        voice = interaction.guild.voice_client
        if voice and voice.channel != channel:
            await voice.move_to(channel)
        elif voice is None:
            voice = await channel.connect()

        self.voice_clients[interaction.guild.id] = voice
        if self._count_non_bot_members(voice) > 0:
            self._cancel_alone_timer(interaction.guild.id)
        return voice

    def _item_from_row(self, row: sqlite3.Row | tuple, requester: str) -> QueueItem:
        title = row["title"] if isinstance(row, sqlite3.Row) else row[1]
        drive_url = row["drive_url"] if isinstance(row, sqlite3.Row) else row[4]
        duration = row["duration"] if isinstance(row, sqlite3.Row) else row[5]
        return QueueItem(
            title=title or "Sem título",
            source_url=drive_url,
            duration=int(duration or 0),
            requested_by=requester,
            origin="db",
        )

    async def _start_next_if_idle(self, guild: discord.Guild):
        guild_id = guild.id
        async with self._guild_lock(guild_id):
            voice = guild.voice_client
            if not voice or voice.is_playing() or voice.is_paused():
                return
            queue = self.queues.get(guild_id, [])
            if not queue:
                self.current_tracks.pop(guild_id, None)
                return
            item = queue.pop(0)
            try:
                source = discord.FFmpegPCMAudio(item.source_url, options="-vn")
                def _after(err: Exception | None):
                    if err:
                        log_to_gui(f"Erro FFmpeg pós-faixa ({guild_id}): {err}", "ERROR")
                    asyncio.run_coroutine_threadsafe(self._start_next_if_idle(guild), self.bot.loop)

                voice.play(source, after=_after)
                self.current_tracks[guild_id] = item
                log_to_gui(f"Tocando em guild {guild_id}: {item.title}", "INFO")
            except FileNotFoundError:
                log_to_gui("FFmpeg não encontrado no ambiente.", "ERROR")
            except Exception as exc:
                log_to_gui(f"Falha ao iniciar reprodução: {exc}", "ERROR")

    async def _search_db(self, term: str, limit: int = 5) -> list[dict[str, Any]]:
        normalized = normalize(term)
        rows = await asyncio.to_thread(_db_fetch_music_candidates, normalized, limit)
        result = []
        for row in rows:
            result.append(
                {
                    "id": row["id"] if isinstance(row, sqlite3.Row) else row[0],
                    "title": row["title"] if isinstance(row, sqlite3.Row) else row[1],
                    "drive_file_id": row["drive_file_id"] if isinstance(row, sqlite3.Row) else row[3],
                    "drive_url": row["drive_url"] if isinstance(row, sqlite3.Row) else row[4],
                    "duration": int((row["duration"] if isinstance(row, sqlite3.Row) else row[5]) or 0),
                    "_row": row,
                }
            )
        return result

    async def save_external_track(self, url: str, info: dict[str, Any]):
        audio_path = None
        try:
            audio_path, title, duration = await asyncio.to_thread(_download_audio_to_mp3, url)
            drive_file_id, drive_url = await asyncio.to_thread(_drive_upload_audio, audio_path, title)
            await asyncio.to_thread(
                _db_insert_music_cache,
                title,
                normalize(title),
                drive_file_id,
                drive_url,
                int(duration or info.get("duration", 0)),
            )
            log_to_gui(f"Faixa salva no Drive/DB: {title}", "SUCCESS")
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    @app_commands.command(name="tocar", description="Toca uma música do banco (Drive).")
    async def tocar(self, interaction: discord.Interaction, busca: str):
        await interaction.response.defer(thinking=True)
        voice = await self._ensure_voice(interaction)
        if not voice or not interaction.guild:
            return

        matches = await self._search_db(busca, limit=5)
        if not matches:
            await interaction.followup.send(
                embed=_music_embed("Nada encontrado", "Nenhuma música corresponde à sua busca.", COLOR_ERROR)
            )
            return

        if len(matches) == 1:
            chosen = matches[0]
        else:
            desc = "\n".join(
                f"**{idx}.** {item['title']} `({_seconds_to_text(item['duration'])})`"
                for idx, item in enumerate(matches, start=1)
            )
            view = TrackChoiceView(matches, interaction.user.id)
            await interaction.followup.send(
                embed=_music_embed("Escolha uma música", desc, COLOR_WAITING),
                view=view,
            )
            chosen = await view.wait_for_selection()
            if not chosen:
                await interaction.followup.send(
                    embed=_music_embed("Tempo esgotado", "A seleção expirou após 30 segundos.", COLOR_ERROR),
                    ephemeral=True,
                )
                return

        item = self._item_from_row(chosen["_row"], interaction.user.display_name)
        self.queues.setdefault(interaction.guild.id, []).insert(0, item)
        await self._start_next_if_idle(interaction.guild)
        await interaction.followup.send(
            embed=_music_embed(
                "Reprodução iniciada",
                f"**{item.title}**\nDuração: `{_seconds_to_text(item.duration)}`",
                COLOR_SUCCESS,
            )
        )

    @app_commands.command(name="tocar_url", description="Toca uma URL externa com yt-dlp.")
    async def tocar_url(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)
        voice = await self._ensure_voice(interaction)
        if not voice or not interaction.guild:
            return

        try:
            info = await asyncio.to_thread(_extract_stream_info, url)
            if not info.get("stream_url"):
                raise RuntimeError("yt-dlp não retornou stream_url.")
        except Exception as exc:
            log_to_gui(f"Erro yt-dlp em /tocar_url: {exc}", "ERROR")
            await interaction.followup.send(
                embed=_music_embed("Falha na URL", f"Não foi possível extrair áudio: `{exc}`", COLOR_ERROR)
            )
            return

        item = QueueItem(
            title=info["title"],
            source_url=info["stream_url"],
            duration=info["duration"],
            requested_by=interaction.user.display_name,
            origin="url",
        )
        self.queues.setdefault(interaction.guild.id, []).insert(0, item)
        await self._start_next_if_idle(interaction.guild)

        view = SaveToDriveView(self, url, info, interaction.user.id)
        await interaction.followup.send(
            embed=_music_embed(
                "Tocando URL externa",
                f"**{info['title']}**\nDuração: `{_seconds_to_text(info['duration'])}`\nDeseja salvar no cache?",
                COLOR_WAITING,
            ),
            view=view,
        )

    @app_commands.command(name="adicionar", description="Adiciona música do banco à fila.")
    async def adicionar(self, interaction: discord.Interaction, busca: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not interaction.guild:
            return
        matches = await self._search_db(busca, limit=1)
        if not matches:
            await interaction.followup.send(
                embed=_music_embed("Nada encontrado", "Nenhuma música encontrada para adicionar.", COLOR_ERROR),
                ephemeral=True,
            )
            return
        item = self._item_from_row(matches[0]["_row"], interaction.user.display_name)
        self.queues.setdefault(interaction.guild.id, []).append(item)
        await interaction.followup.send(
            embed=_music_embed("Adicionada na fila", f"**{item.title}** foi adicionada com sucesso.", COLOR_SUCCESS),
            ephemeral=True,
        )
        await self._start_next_if_idle(interaction.guild)

    @app_commands.command(name="adicionar_url", description="Adiciona uma URL externa à fila.")
    async def adicionar_url(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not interaction.guild:
            return
        try:
            info = await asyncio.to_thread(_extract_stream_info, url)
        except Exception as exc:
            await interaction.followup.send(
                embed=_music_embed("Falha na URL", f"Não foi possível processar: `{exc}`", COLOR_ERROR),
                ephemeral=True,
            )
            return
        item = QueueItem(
            title=info.get("title", "Sem título"),
            source_url=info.get("stream_url", ""),
            duration=int(info.get("duration", 0)),
            requested_by=interaction.user.display_name,
            origin="url",
        )
        if not item.source_url:
            await interaction.followup.send(
                embed=_music_embed("Falha na URL", "A URL não retornou stream reproduzível.", COLOR_ERROR),
                ephemeral=True,
            )
            return
        self.queues.setdefault(interaction.guild.id, []).append(item)
        await interaction.followup.send(
            embed=_music_embed("Adicionada na fila", f"**{item.title}** entrou na fila.", COLOR_SUCCESS),
            ephemeral=True,
        )
        await self._start_next_if_idle(interaction.guild)

    @app_commands.command(name="pausar", description="Pausa a música atual.")
    async def pausar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not voice.is_playing():
            await interaction.followup.send(
                embed=_music_embed("Nada tocando", "Não há reprodução ativa para pausar.", COLOR_ERROR), ephemeral=True
            )
            return
        voice.pause()
        await interaction.followup.send(embed=_music_embed("Pausado", "Reprodução pausada.", COLOR_INFO), ephemeral=True)

    @app_commands.command(name="retomar", description="Retoma a música pausada.")
    async def retomar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not voice.is_paused():
            await interaction.followup.send(
                embed=_music_embed("Nada pausado", "Não há música pausada no momento.", COLOR_ERROR), ephemeral=True
            )
            return
        voice.resume()
        await interaction.followup.send(embed=_music_embed("Retomado", "Reprodução retomada.", COLOR_SUCCESS), ephemeral=True)

    @app_commands.command(name="parar", description="Para a reprodução e desconecta o bot.")
    async def parar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return
        voice = interaction.guild.voice_client
        if not voice:
            await interaction.followup.send(
                embed=_music_embed("Sem conexão", "O bot não está em canal de voz.", COLOR_ERROR), ephemeral=True
            )
            return
        guild_id = interaction.guild.id
        voice.stop()
        await voice.disconnect()
        self._cleanup_guild_state(guild_id)
        await interaction.followup.send(
            embed=_music_embed("Parado", "Reprodução encerrada e bot desconectado.", COLOR_SUCCESS), ephemeral=True
        )

    @app_commands.command(name="pular", description="Pula para a próxima música da fila.")
    async def pular(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not (voice.is_playing() or voice.is_paused()):
            await interaction.followup.send(
                embed=_music_embed("Nada tocando", "Não há faixa ativa para pular.", COLOR_ERROR), ephemeral=True
            )
            return
        voice.stop()
        await interaction.followup.send(
            embed=_music_embed("Pulada", "Faixa atual pulada. Próxima será iniciada.", COLOR_INFO), ephemeral=True
        )

    @app_commands.command(name="fila", description="Mostra a fila atual.")
    async def fila(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_music_embed("Sem guild", "Comando disponível apenas em servidor.", COLOR_ERROR), ephemeral=True
            )
            return
        queue = self.queues.get(interaction.guild.id, [])
        view = QueuePaginationView(queue)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # Só precisamos reagir a mudanças em guilds onde o bot está conectado.
        guild = member.guild
        voice = guild.voice_client
        if not voice:
            return

        guild_id = guild.id

        # Se o bot saiu/desconectou, limpar estado residual.
        if member.id == self.bot.user.id and after.channel is None:
            self._cleanup_guild_state(guild_id)
            return

        # Se alguém humano entrou no canal do bot, cancela timer de inatividade.
        if after.channel and voice.channel and after.channel.id == voice.channel.id and not member.bot:
            self._cancel_alone_timer(guild_id)
            return

        # Se alguém saiu/trocou de canal e o bot ficou sozinho, agenda auto-disconnect.
        if before.channel and voice.channel and before.channel.id == voice.channel.id:
            if self._count_non_bot_members(voice) == 0:
                self._schedule_alone_timer(guild_id)


def _populate_normalized_titles_if_empty():
    """Normaliza os títulos no music_cache que ainda não têm normalized_title preenchido."""
    try:
        updated = _db_update_missing_normalized_titles()
        log_to_gui(f"Normalização concluída. Registros atualizados: {updated}", "INFO")
    except Exception as exc:
        log_to_gui(f"Erro em _populate_normalized_titles_if_empty: {exc}", "ERROR")


async def rebuild_database_from_drive(force: bool = False):
    """Varre o Google Drive em busca de arquivos de áudio e reconstrói/atualiza o music_cache."""
    conn = _get_db_conn()
    cur = conn.cursor()

    try:
        files = await asyncio.to_thread(_drive_list_audio_files)
        if force:
            await asyncio.to_thread(cur.execute, "DELETE FROM music_cache")
            await asyncio.to_thread(conn.commit)

        inserted = 0
        for file in files:
            file_id = file.get("id", "")
            name = file.get("name", "Sem título")
            title = os.path.splitext(name)[0]
            drive_url = file.get("webContentLink") or f"https://drive.google.com/uc?export=download&id={file_id}"

            def _insert_or_ignore():
                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO music_cache
                    (title, normalized_title, drive_file_id, drive_url, duration, added_at)
                    SELECT ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM music_cache WHERE drive_file_id = ?
                    )
                    """,
                    (
                        title,
                        normalize(title),
                        file_id,
                        drive_url,
                        0,
                        datetime.utcnow().isoformat(),
                        file_id,
                    ),
                )
                conn.commit()
                return c.rowcount

            inserted += int(await asyncio.to_thread(_insert_or_ignore) or 0)

        log_to_gui(f"Rebuild do Drive finalizado. Novos registros: {inserted}", "SUCCESS")
    except Exception as exc:
        log_to_gui(f"Erro em rebuild_database_from_drive: {exc}", "ERROR")


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicaV2(bot))
