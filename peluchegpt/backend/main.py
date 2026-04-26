"""Servidor FastAPI principal do PelucheGPT."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .chat_engine import process_message
from .config import AppConfig, config_to_dict, load_config, save_config
from .db import get_bot_stats, query_lore_search
from .lore_indexer import lore_indexer


app = FastAPI(title="PelucheGPT Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Processo do bot Discord (global para controle de ciclo de vida)
_bot_process: subprocess.Popen | None = None


# ── MODELS ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Payload de entrada para o endpoint de chat."""
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, Any]] = Field(default_factory=list)


class ConfigUpdateRequest(BaseModel):
    """Payload parcial para atualização de configuração."""
    sqlite_path: str | None = None
    gemini_api_key: str | None = None
    ollama_model: str | None = None
    ollama_url: str | None = None
    ollama_num_gpu: int | None = None
    ollama_num_threads: int | None = None
    ollama_num_ctx: int | None = None
    ollama_num_keep: int | None = None
    complexity_threshold: float | None = None
    max_context_chunks: int | None = None
    theme: str | None = None
    discord_token: str | None = None
    bot_path: str | None = None
    drive_folder_id: str | None = None


# ── STARTUP ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event() -> None:
    """Dispara indexação inicial em segundo plano sem bloquear a API."""
    load_config()
    asyncio.create_task(lore_indexer.ensure_indexed(force=False))


# ── CHAT ──────────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    """Responde mensagens do frontend via pipeline híbrido."""
    return await process_message(payload.message, payload.history)


# ── STATUS ────────────────────────────────────────────────────────────────

@app.get("/status")
async def status() -> dict[str, Any]:
    """Retorna saúde dos componentes locais e cloud."""
    cfg = load_config()

    ollama_ok = False
    gemini_ok = bool(cfg.gemini_api_key)
    sqlite_ok = Path(cfg.sqlite_path).exists()

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{cfg.ollama_url}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False

    return {
        "backend": "ok",
        "sqlite": sqlite_ok,
        "ollama": ollama_ok,
        "gemini_configured": gemini_ok,
        "chromadb": lore_indexer.stats(),
    }


# ── LORE ──────────────────────────────────────────────────────────────────

@app.get("/lore/stats")
async def lore_stats() -> dict[str, Any]:
    """Exibe estatísticas de indexação do lore."""
    return lore_indexer.stats()


@app.post("/lore/reindex")
async def lore_reindex() -> dict[str, Any]:
    """Força reindexação completa do lore do SQLite."""
    return await lore_indexer.ensure_indexed(force=True)


@app.get("/lore/list")
async def lore_list(search: str = "", limit: int = 80) -> list[dict[str, Any]]:
    """Lista lore para tabela navegável no frontend."""
    return query_lore_search(search, limit=min(max(limit, 1), 500))


# ── CONFIG ────────────────────────────────────────────────────────────────

@app.get("/config")
async def get_config() -> dict[str, Any]:
    """Retorna configuração atual para preencher formulário da UI."""
    return config_to_dict(load_config())


@app.post("/config")
async def post_config(payload: ConfigUpdateRequest) -> dict[str, Any]:
    """Atualiza config.json com os campos enviados pelo frontend."""
    current = load_config()
    data = current.__dict__.copy()
    for key, value in payload.model_dump(exclude_none=True).items():
        data[key] = value

    try:
        updated = AppConfig(**data)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"Config inválida: {exc}") from exc

    save_config(updated)
    return {"ok": True, "config": config_to_dict(updated)}


# ── BOT DISCORD ───────────────────────────────────────────────────────────

@app.get("/bot/status")
async def bot_status() -> dict[str, Any]:
    """Verifica se o processo do bot Discord está rodando."""
    global _bot_process
    
    if _bot_process is None:
        return {"running": False, "pid": None, "output": "Processo não iniciado"}
    
    # Lê output do processo pra debug
    returncode = _bot_process.poll()
    output = ""
    if returncode is not None:
        # Processo terminou — lê o que ele imprimiu
        try:
            out, _ = _bot_process.communicate(timeout=1)
            output = out[:500] if out else ""
        except Exception:
            pass
    
    running = returncode is None
    return {
        "running": running,
        "pid": _bot_process.pid if running else None,
        "returncode": returncode,
        "output": output
    }

@app.post("/bot/start")
async def bot_start() -> dict[str, Any]:
    """Inicia o bot Discord como subprocesso."""
    global _bot_process

    if _bot_process is not None and _bot_process.poll() is None:
        return {"ok": True, "already_running": True, "pid": _bot_process.pid}

    cfg = load_config()
    bot_path = Path(getattr(cfg, "bot_path", "") or "C:/P3-LUCH3/main.py")
    python_path = Path(getattr(cfg, "python_path", "") or "C:/P3-LUCH3/.venv/Scripts/python.exe")

    if not bot_path.exists():
        raise HTTPException(status_code=400, detail=f"main.py não encontrado em: {bot_path}")

    if not python_path.exists():
        raise HTTPException(status_code=400, detail=f"Python não encontrado em: {python_path}")

    try:
        import os
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"  # <- corrige o erro de emoji

        _bot_process = subprocess.Popen(
            [str(python_path), str(bot_path)],
            cwd=str(bot_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        return {"ok": True, "pid": _bot_process.pid}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao iniciar bot: {exc}") from exc


@app.post("/bot/stop")
async def bot_stop() -> dict[str, Any]:
    """Para o processo do bot Discord."""
    global _bot_process

    if _bot_process is None or _bot_process.poll() is not None:
        return {"ok": True, "already_stopped": True}

    try:
        _bot_process.terminate()
        try:
            _bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _bot_process.kill()
        _bot_process = None
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao parar bot: {exc}") from exc


# ── BOT STATS ─────────────────────────────────────────────────────────────

@app.get("/bot/stats")
async def bot_stats_endpoint() -> dict[str, Any]:
    """Retorna estatísticas gerais do banco do bot."""
    try:
        return get_bot_stats()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc