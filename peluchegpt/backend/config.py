"""Configurações centrais do PelucheGPT."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


APP_DIR = Path.home() / ".peluchegpt"
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.json"
CHROMA_DIR = APP_DIR / "chroma_db"
INDEX_META_PATH = APP_DIR / "index_meta.json"


@dataclass
class AppConfig:
    """Representa todas as opções de configuração editáveis na UI."""

    sqlite_path: str = "C:/P3-LUCH3/database.db"
    gemini_api_key: str = ""
    ollama_model: str = "mistral"
    ollama_url: str = "http://localhost:11434"
    complexity_threshold: float = 0.7
    max_context_chunks: int = 5
    theme: str = "dark"


def _ensure_base_files() -> None:
    """Garante a estrutura mínima de diretórios e arquivos do app."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text("[]", encoding="utf-8")


def load_config() -> AppConfig:
    """Carrega o config.json do usuário ou cria o padrão."""
    _ensure_base_files()
    if not CONFIG_PATH.exists():
        default_cfg = AppConfig()
        save_config(default_cfg)
        return default_cfg

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return AppConfig(**{**asdict(AppConfig()), **data})


def save_config(config: AppConfig) -> None:
    """Persiste as configurações do usuário no diretório local."""
    _ensure_base_files()
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    """Converte o dataclass para dicionário serializável."""
    return asdict(config)
