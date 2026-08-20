"""Painel de status ao vivo no terminal (estilo fastfetch).

Arte animada à esquerda, dados do bot em tempo real à direita, ancorados numa
região fixa do terminal enquanto os logs normais continuam rolando acima.

Como convive com os logs
------------------------
O `Live` do rich é iniciado com `redirect_stdout`/`redirect_stderr`, então todo
`print()` cru (é o que `utils.log_to_gui` usa) e todo log do discord.py passam a
ser impressos ACIMA do painel em vez de brigar com ele pelo cursor. Nada precisou
mudar no `log_to_gui`.

Por isso o `Live` precisa começar ANTES de `bot.run()`: o `setup_logging()` do
discord.py cria um StreamHandler que captura `sys.stderr` no momento da
construção — se o redirect não estiver de pé ainda, os logs dele escapam do
painel e embaralham a tela. Ver `dashboard_session()` em `main.py`.

Desligar
--------
`P3LUCHE_DASHBOARD=0` desliga. Sem a variável, liga sozinho só quando a saída é
um terminal de verdade (`isatty`), então redirecionar para arquivo ou rodar em
CI não tenta desenhar painel nenhum. `P3LUCHE_DASHBOARD=1` força ligado.

Segurança
---------
Qualquer falha aqui é engolida: o painel some, o bot continua. Nenhum caminho
deste módulo pode propagar exceção para o bot.
"""
import contextlib
import os
import random
import sys
from datetime import datetime

from discord.ext import commands, tasks

import telemetry
from cogs.dashboard_animation import TVAnimation
from cogs.dashboard_panel import (
    ACTIVITY_WINDOW_SECONDS,
    DashboardData,
    build_dashboard,
    collect_connection,
    should_redraw,
)

#: Frame rate da animação. Baixo de propósito — o glitch de CRT funciona bem
#: assim e o custo por frame fica irrelevante perto do resto do bot.
FPS = 8

ENV_FLAG = "P3LUCHE_DASHBOARD"

#: Quantos erros recentes listar no painel.
ERROR_LIST_SIZE = 3

_live = None
_error_handler = None
_process_start = datetime.now()


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


class DashboardCog(commands.Cog):
    """Redesenha o painel a cada frame com dados frescos."""

    def __init__(self, bot):
        self.bot = bot
        self.rng = random.Random()
        self.animation = TVAnimation(fps=FPS, rng=self.rng)

        process = None
        try:
            import psutil

            process = psutil.Process()
            # Primeira chamada só estabelece a linha de base do cpu_percent.
            process.cpu_percent(None)
        except Exception:
            process = None

        self.data = DashboardData(process=process, process_start=_process_start)
        self._last_second = None
        self._was_glitching = False

    async def cog_load(self):
        if _live is not None and not self.refresh_loop.is_running():
            self.refresh_loop.start()

    def cog_unload(self):
        if self.refresh_loop.is_running():
            self.refresh_loop.cancel()

    @tasks.loop(seconds=1 / FPS)
    async def refresh_loop(self):
        try:
            self._render_once()
        except Exception:
            # Painel quebrado não pode derrubar nem travar o bot.
            pass

    def _render_once(self) -> None:
        live = _live
        if live is None:
            return

        now = datetime.now()

        # Ponte com o pipeline de erro existente: o handler anexado ao logger do
        # erros.py alimenta a telemetria, e a animação só lê o timestamp daqui.
        last_error = telemetry.last_error_at()
        if last_error is not None:
            self.animation.notify_error(last_error.timestamp())

        frame = self.animation.tick(now.timestamp())

        # Em repouso o painel só muda quando o segundo vira; redesenhar a 8 fps
        # ali seria queimar CPU reimprimindo a mesma tela.
        current_second = now.replace(microsecond=0)
        redraw = should_redraw(
            frame.has_glitch, self._was_glitching, current_second, self._last_second
        )
        self._was_glitching = frame.has_glitch
        if not redraw:
            return
        self._last_second = current_second

        renderable = build_dashboard(
            frame=frame,
            rng=self.rng,
            connection=collect_connection(self.bot, now, _process_start),
            stats=self.data.process_stats(now),
            economy=self.data.economy(getattr(self.bot, "db_conn", None), now),
            interactions=telemetry.recent_interactions(ACTIVITY_WINDOW_SECONDS, now),
            errors=telemetry.recent_errors(ERROR_LIST_SIZE),
            error_count_hour=telemetry.error_count(3600, now),
        )
        live.update(renderable, refresh=True)


async def setup(bot):
    await bot.add_cog(DashboardCog(bot))
