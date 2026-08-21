"""Bootstrap de isolamento do banco para a suíte de testes.

Este arquivo existe para uma única coisa: garantir que nenhum teste toque o
database/bot.db de produção. Ele é importado pelo unittest antes de qualquer
módulo de teste (discovery importa o pacote `tests` primeiro), e o conftest.py
ao lado apenas o reimporta para cobrir também quem rodar com pytest.

Por que aponta para uma variável de ambiente em vez de monkeypatch: config.py
da raiz é um shim ("from src.p3luche.config import *"), então existem dois
objetos de módulo distintos — `config` e `src.p3luche.config` — cada um com sua
própria cópia de DB_PATH. Repontar um em runtime deixa o outro apontando para
produção. Definir o env ANTES do primeiro import faz os dois nascerem já com o
caminho descartável, e o mesmo vale para todo binding derivado copiado no
import (database.db_manager, cogs.admin, cogs.backup, migration_v4).
"""
import atexit
import os
import shutil
import sys
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="p3luche-tests-")
_TMP_DB = os.path.join(_TMP_DIR, "bot_test.db")

os.environ["P3LUCHE_DB_PATH"] = _TMP_DB

# Se config já entrou em sys.modules, DB_PATH foi resolvido antes do override e
# aponta para produção. Falhar alto é melhor que rodar a suíte contra o banco
# real sem ninguém perceber.
_ja_importado = [m for m in ("config", "src.p3luche.config") if m in sys.modules]
if _ja_importado:
    raise RuntimeError(
        f"{', '.join(_ja_importado)} importado antes do isolamento de banco em "
        "tests/__init__.py; DB_PATH pode apontar para database/bot.db de produção."
    )


@atexit.register
def _limpar_banco_temporario() -> None:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
