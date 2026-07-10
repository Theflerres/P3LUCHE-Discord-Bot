"""
Backup automático do SQLite para o Google Drive (executado em thread para não bloquear o loop).
"""
import asyncio
import os
from datetime import datetime

from discord.ext import commands, tasks

from config import DB_PATH, DRIVE_FOLDER_ID, LOG_FOLDER, get_bot_instance, set_bot_instance
from utils import log_to_gui


def _upload_db_sync(local_path: str, remote_name: str) -> str:
    """Upload síncrono (executar via asyncio.to_thread)."""
    from cogs.musica import get_drive_service
    from googleapiclient.http import MediaFileUpload

    if not DRIVE_FOLDER_ID:
        raise RuntimeError("DRIVE_FOLDER_ID não configurado.")
    service = get_drive_service()
    if not service:
        raise RuntimeError("Google Drive não autenticado.")
    metadata = {"name": remote_name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(local_path, mimetype="application/x-sqlite3", resumable=True)
    file = service.files().create(body=metadata, media_body=media, fields="id").execute()
    fid = file.get("id")
    service.permissions().create(fileId=fid, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/file/d/{fid}/view"


class BackupCog(commands.Cog):
    """Envia `bot.db` para o Drive uma vez por dia (ajuste o horário com o relógio do processo)."""

    def __init__(self, bot):
        self.bot = bot
        set_bot_instance(bot)

    async def cog_load(self):
        if not self.backup_loop.is_running():
            self.backup_loop.start()

    def cog_unload(self):
        self.backup_loop.cancel()

    @tasks.loop(hours=24)
    async def backup_loop(self):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        remote = f"bot_backup_{ts}.db"
        try:
            link = await asyncio.to_thread(_upload_db_sync, DB_PATH, remote)
            log_to_gui(f"Backup automático enviado ao Drive: {remote} → {link}", "SUCCESS")
        except Exception as e:
            log_to_gui(f"Falha no backup automático: {e}", "ERROR")

    @backup_loop.before_loop
    async def before_backup(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BackupCog(bot))
