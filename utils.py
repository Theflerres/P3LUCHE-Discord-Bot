"""
Funções utilitárias isoladas — logging, texto, anexos e helpers visuais.
"""
import os
import re
from datetime import timedelta
from io import BytesIO

import discord
import docx
import pypdf
import requests
from PIL import Image

from config import LOG_FOLDER


def log_to_gui(message, level="INFO"):
    """Log colorido no terminal (ANSI)."""
    from datetime import datetime

    timestamp = datetime.now().strftime("%H:%M:%S")
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "ERROR": "\033[91m",
        "WARNING": "\033[93m",
        "WAKEUP": "\033[96m",
        "SLEEP": "\033[90m",
    }
    reset = "\033[0m"
    color_code = colors.get(level, "\033[97m")

    print(f"{color_code}[{timestamp}] [{level}] {message}{reset}")


async def extract_text_from_attachment(attachment: discord.Attachment) -> str:
    """Extrai texto de PDF, DOCX, TXT ou MD."""
    filename = attachment.filename.lower()
    try:
        file_bytes = await attachment.read()
        file_stream = BytesIO(file_bytes)
        extracted_text = ""
        if filename.endswith(".pdf"):
            reader = pypdf.PdfReader(file_stream)
            for page in reader.pages:
                extracted_text += page.extract_text() + "\n"
        elif filename.endswith(".docx"):
            doc = docx.Document(file_stream)
            extracted_text = "\n".join([para.text for para in doc.paragraphs])
        elif filename.endswith(".txt") or filename.endswith(".md"):
            extracted_text = file_bytes.decode("utf-8")
        else:
            return ""
        return extracted_text.strip()
    except Exception as e:
        log_to_gui(f"Erro ao ler arquivo {filename}: {e}", "ERROR")
        return f"[Erro ao ler arquivo: {e}]"


def get_local_file(path, filename):
    """Tenta carregar um arquivo local. Retorna (File, attachment_str) ou (None, None)."""
    if os.path.exists(path):
        return discord.File(path, filename=filename), f"attachment://{filename}"
    return None, None


def sanitize_text(text: str) -> str:
    """Limpa o texto de entrada para evitar injeções simples e caracteres nulos."""
    if not text:
        return ""
    clean = text.replace("\x00", "").strip()
    return clean[:1500]


def normalize_title(title: str) -> str:
    """Normaliza título de música para busca e ordenação."""
    if not title:
        return ""
    norm_title = title.lower()
    norm_title = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", norm_title)
    keywords = [
        "official music video",
        "music video",
        "official video",
        "official audio",
        "lyric video",
        "lyrics",
        "legendado",
        "tradução",
        "traduzido",
        "hd",
        "4k",
        "hq",
        "clipe oficial",
        "vídeo oficial",
        "áudio oficial",
        "full album",
        "ao vivo",
        "live",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "|",
        "-",
        "_",
        '"',
        "'",
    ]
    for keyword in keywords:
        norm_title = norm_title.replace(keyword, "")
    return re.sub(r"\s+", " ", norm_title).strip()


def format_timedelta(delta: timedelta) -> str:
    """Formata timedelta em texto legível (dias, horas, minutos)."""
    days, rem = divmod(delta.total_seconds(), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    days, hours, minutes = int(days), int(hours), int(minutes)
    parts = []
    if days > 0:
        parts.append(f"{days} dia{'s' if days > 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hora{'s' if hours > 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minuto{'s' if minutes > 1 else ''}")
    return ", ".join(parts) if parts else "alguns segundos"


def extract_youtube_id(url):
    """Extrai ID de vídeo de URL do YouTube."""
    patterns = [
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com.*[?&]v=)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def get_best_thumbnail(video_id):
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"


def get_thumbnail_dominant_color(url):
    try:
        response = requests.get(url, timeout=5)
        img = Image.open(BytesIO(response.content)).convert("RGB").resize((50, 50))
        pixels = list(img.getdata())
        return discord.Color.from_rgb(*max(set(pixels), key=pixels.count))
    except Exception:
        return discord.Color.blurple()


# Caminho de cookies Brave exportado (usado por yt-dlp na cog de música)
COOKIE_FILE = os.path.join(LOG_FOLDER, "cookies.txt")
