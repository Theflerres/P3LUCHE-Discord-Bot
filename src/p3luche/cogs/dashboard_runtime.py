"""Sessão do painel de status — estado vivo, fora do módulo de extensão.

Por que este módulo existe (não junte com `cogs/dashboard.py`)
--------------------------------------------------------------
`bot.load_extension("cogs.dashboard")` NÃO reaproveita o módulo já importado.
O discord.py faz, em `_load_from_module_spec`:

    lib = importlib.util.module_from_spec(spec)   # objeto de módulo NOVO
    sys.modules[key] = lib                        # substitui o cache
    spec.loader.exec_module(lib)                  # re-executa do zero

Ou seja, qualquer estado guardado nos globais do módulo de extensão é criado
duas vezes: uma na cópia que o `main.py` importou, outra na cópia que virou o
cog. Foi exatamente esse o bug do painel que nunca desenhava — o `main.py`
setava `_live` na primeira cópia, e o cog lia `_live` (ainda None) da segunda.

Este módulo nunca é carregado como extensão, então tem uma instância só. Ele é
a fonte de verdade do `Live`; o cog consulta via `get_live()`.
"""
import contextlib
import os
import sys
from datetime import datetime

import telemetry

ENV_FLAG = "P3LUCHE_DASHBOARD"

#: Início do processo — usado como fallback de uptime antes de `bot.start_time`.
#: Fica aqui (e não no módulo de extensão) para não ser reiniciado quando o
#: discord.py re-executa a extensão.
PROCESS_START = datetime.now()

_live = None
_error_handler = None


def get_live():
    """O `Live` ativo, ou None se o painel está desligado/não iniciado."""
    return _live


def is_enabled() -> bool:
    """Painel ligado? Variável explícita vence; senão, só em terminal real."""
    raw = os.getenv(ENV_FLAG)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "on", "yes", "sim")
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


@contextlib.contextmanager
def dashboard_session():
    """Mantém o painel vivo enquanto o bot roda. No-op se estiver desligado.

    Usado por `main.py` envolvendo `bot.run()`. Se qualquer coisa falhar ao
    iniciar, cede o controle sem painel — o bot roda normalmente.
    """
    global _live, _error_handler

    if not is_enabled():
        yield None
        return

    try:
        from rich.console import Console
        from rich.live import Live

        console = Console()
        _live = Live(
            console=console,
            auto_refresh=False,      # quem controla o frame rate é o cog
            redirect_stdout=True,    # logs do log_to_gui aparecem acima do painel
            redirect_stderr=True,    # idem para o logging do discord.py
            vertical_overflow="visible",
        )
        _error_handler = telemetry.attach_error_capture()
        _live.start()
    except Exception as exc:
        _live = None
        print(f"[painel] desativado (falha ao iniciar: {exc})")
        yield None
        return

    try:
        yield _live
    finally:
        try:
            _live.stop()
        except Exception:
            pass
        telemetry.detach_error_capture(_error_handler)
        _live = None
        _error_handler = None
