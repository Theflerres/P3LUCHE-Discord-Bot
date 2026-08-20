"""Estado de runtime compartilhado (telemetria em memória) do P3LUCHE.

Guarda, em buffers circulares pequenos, dois sinais que o painel de status do
terminal consome: as interações recentes (quem está usando o bot agora) e os
erros recentes.

Duas decisões importantes:

1. A API é toda em funções, não em containers expostos. O projeto tem shims na
   raiz (`telemetry.py` → `from src.p3luche.telemetry import *`) para os testes
   rodarem a partir da raiz do repo; `import *` copia nomes, então expor os
   deques direto criaria DOIS buffers distintos dependendo do caminho de import.
   Funções sempre mutam o global do módulo onde foram definidas, então o estado
   fica em um lugar só — mesmo padrão já usado por `config.set_bot_instance`.

2. Erros não são capturados por um pipeline novo: `cogs/erros.py` já funila todo
   erro de comando/listener pelo logger "P3LUCHE_ERROS". Aqui só anexamos um
   `logging.Handler` a esse mesmo logger (ver `attach_error_capture`), sem tocar
   no `erros.py`.
"""
import logging
import threading
from collections import deque
from datetime import datetime, timedelta

# Buffers pequenos e limitados: isso é decoração de terminal, não deve virar
# uma fonte de vazamento de memória num bot que roda por dias.
MAX_INTERACTIONS = 50
MAX_ERRORS = 20

# Níveis que fazem a "TV" do painel entrar em estado de erro (vermelho).
ERROR_LEVELS = ("ERROR", "CRITICAL")

ERROR_LOGGER_NAME = "P3LUCHE_ERROS"

_lock = threading.Lock()
_interactions: deque = deque(maxlen=MAX_INTERACTIONS)
_errors: deque = deque(maxlen=MAX_ERRORS)


# ──────────────────────────────────────────────
#  INTERAÇÕES (quem está usando o bot agora)
# ──────────────────────────────────────────────

def record_interaction(user_name: str, command_name: str, when: datetime = None) -> None:
    """Registra uma interação. Nunca levanta exceção: é telemetria, não fluxo."""
    try:
        entry = {
            "when": when or datetime.now(),
            "user_name": str(user_name or "?"),
            "command_name": str(command_name or "?"),
        }
        with _lock:
            _interactions.append(entry)
    except Exception:
        pass


def recent_interactions(window_seconds: int = 600, now: datetime = None, limit: int = 5) -> list:
    """Interações dentro da janela, mais recentes primeiro.

    Deduplica por (usuário, comando) mantendo a ocorrência mais recente, para o
    painel não virar cinco linhas da mesma pessoa repetindo /eco pescar.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    with _lock:
        snapshot = list(_interactions)

    # Ordena por horário em vez de confiar na ordem de inserção: em produção as
    # duas coincidem, mas depender disso deixaria a ordem do painel à mercê de
    # qualquer registro fora de ordem.
    snapshot.sort(key=lambda entry: entry["when"])

    seen = set()
    out = []
    for entry in reversed(snapshot):
        if entry["when"] < cutoff:
            continue
        key = (entry["user_name"], entry["command_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return out


def active_user_count(window_seconds: int = 600, now: datetime = None) -> int:
    """Quantidade de usuários distintos que interagiram na janela."""
    now = now or datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    with _lock:
        snapshot = list(_interactions)
    return len({e["user_name"] for e in snapshot if e["when"] >= cutoff})


# ──────────────────────────────────────────────
#  ERROS
# ──────────────────────────────────────────────

def record_error(message: str, level: str = "ERROR", when: datetime = None) -> None:
    """Registra um erro. Nunca levanta exceção."""
    try:
        text = str(message or "").strip().replace("\n", " ")
        entry = {
            "when": when or datetime.now(),
            "level": str(level or "ERROR").upper(),
            "message": text,
        }
        with _lock:
            _errors.append(entry)
    except Exception:
        pass


def recent_errors(limit: int = 3) -> list:
    """Últimos erros registrados, mais recentes primeiro."""
    with _lock:
        snapshot = list(_errors)
    snapshot.sort(key=lambda entry: entry["when"], reverse=True)
    return snapshot[:limit] if limit is not None else snapshot


def error_count(window_seconds: int = 3600, now: datetime = None) -> int:
    """Quantos erros foram registrados na janela."""
    now = now or datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    with _lock:
        snapshot = list(_errors)
    return sum(1 for e in snapshot if e["when"] >= cutoff)


def last_error_at(levels: tuple = ERROR_LEVELS) -> datetime:
    """Timestamp do erro mais recente com nível em `levels`, ou None.

    É o que dispara o estado vermelho da animação — WARNING aparece na lista de
    erros do painel, mas não deixa a TV vermelha.
    """
    with _lock:
        snapshot = list(_errors)
    candidates = [entry["when"] for entry in snapshot if entry["level"] in levels]
    return max(candidates) if candidates else None


def reset() -> None:
    """Zera os buffers (usado pelos testes)."""
    with _lock:
        _interactions.clear()
        _errors.clear()


# ──────────────────────────────────────────────
#  CAPTURA DE ERRO VIA LOGGER EXISTENTE
# ──────────────────────────────────────────────

class TelemetryLogHandler(logging.Handler):
    """Espelha registros do logger de erros para o buffer de telemetria.

    Anexado ao logger que `cogs/erros.py` já usa — não é um segundo pipeline de
    erro, é uma derivação do que já existe.
    """

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Só a primeira linha: o logger de erros grava stack traces enormes,
            # e o painel tem uma linha por erro.
            message = record.getMessage().strip().splitlines()
            headline = message[0] if message else record.levelname
            record_error(headline, record.levelname)
        except Exception:
            pass  # logging nunca pode derrubar o bot


def attach_error_capture(logger_name: str = ERROR_LOGGER_NAME) -> logging.Handler:
    """Anexa a captura ao logger de erros existente e devolve o handler."""
    handler = TelemetryLogHandler()
    logging.getLogger(logger_name).addHandler(handler)
    return handler


def detach_error_capture(handler: logging.Handler, logger_name: str = ERROR_LOGGER_NAME) -> None:
    """Remove a captura (usado ao encerrar o painel)."""
    if handler is None:
        return
    try:
        logging.getLogger(logger_name).removeHandler(handler)
    except Exception:
        pass
