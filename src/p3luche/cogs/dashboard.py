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
painel e embaralham a tela. Ver `dashboard_session()` em `cogs/dashboard_runtime.py`.

Onde mora o estado
------------------
O `Live` fica em `cogs/dashboard_runtime.py`, e este módulo o consulta via
`get_live()`. Isso NÃO é indireção gratuita: `load_extension` re-executa o
módulo de extensão num objeto novo, então estado guardado aqui existiria em duas
cópias e o cog leria sempre a errada. Ver a explicação completa lá.

Desligar
--------
`P3LUCHE_DASHBOARD=0` desliga. Sem a variável, liga sozinho só quando a saída é
um terminal de verdade (`isatty`), então redirecionar para arquivo ou rodar em
CI não tenta desenhar painel nenhum. `P3LUCHE_DASHBOARD=1` força ligado.

Segurança
---------
Uma falha de render desliga o painel e reporta uma vez; o bot continua. Nenhum
caminho deste módulo pode propagar exceção para o bot.
"""
import random
from datetime import datetime

from discord.ext import commands, tasks

import telemetry
from utils import log_to_gui
from cogs.dashboard_animation import TVAnimation
from cogs.dashboard_panel import (
    ACTIVITY_WINDOW_SECONDS,
    DashboardData,
    build_dashboard,
    collect_connection,
    should_redraw,
)
from cogs.dashboard_runtime import PROCESS_START, get_live

#: Frame rate da animação. Baixo de propósito — o glitch de CRT funciona bem
#: assim e o custo por frame fica irrelevante perto do resto do bot.
FPS = 8

#: Quantos erros recentes listar no painel.
ERROR_LIST_SIZE = 3


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

        self.data = DashboardData(process=process, process_start=PROCESS_START)
        self._last_second = None
        self._was_glitching = False
        self._render_failed = False

    async def cog_load(self):
        if get_live() is None:
            log_to_gui(
                "Painel: sessão não iniciada (P3LUCHE_DASHBOARD desligado ou "
                "saída não é um terminal) — cog carregado sem desenhar.",
                "INFO",
            )
            return
        if not self.refresh_loop.is_running():
            self.refresh_loop.start()

    def cog_unload(self):
        if self.refresh_loop.is_running():
            self.refresh_loop.cancel()

    @tasks.loop(seconds=1 / FPS)
    async def refresh_loop(self):
        try:
            self._render_once()
        except Exception as exc:
            # Painel quebrado não pode derrubar nem travar o bot — mas engolir
            # em silêncio esconde a falha para sempre (foi exatamente assim que
            # um painel que nunca desenhava passou despercebido). Reporta a
            # primeira falha e só então silencia, para não poluir 8x por segundo.
            if not self._render_failed:
                self._render_failed = True
                log_to_gui(f"Painel desativado após erro no render: {exc!r}", "ERROR")
                self.refresh_loop.cancel()

    def _render_once(self) -> None:
        # Sempre via get_live(): o estado vive em `dashboard_runtime`, que não é
        # extensão e por isso não é re-executado pelo load_extension.
        live = get_live()
        if live is None:
            return

        now = datetime.now()

        # Ponte com o pipeline de erro existente: o handler anexado ao logger do
        # erros.py alimenta a telemetria, e a animação só lê o timestamp daqui.
        last_error = telemetry.last_error_at()
        if last_error is not None:
            self.animation.notify_error(last_error.timestamp())

        frame = self.animation.tick(now.timestamp())

        connection = collect_connection(self.bot, now, PROCESS_START)
        # Alimentado todo frame, mas só registra leitura nova quando o valor
        # muda — é assim que o painel sabe há quanto tempo aquele número está
        # parado (o gateway só manda heartbeat a cada ~41s).
        self.data.latency.update(connection["latency_ms"], now)

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
            connection=connection,
            latency_tracker=self.data.latency,
            now=now,
            stats=self.data.process_stats(now),
            economy=self.data.economy(getattr(self.bot, "db_conn", None), now),
            interactions=telemetry.recent_interactions(ACTIVITY_WINDOW_SECONDS, now),
            errors=telemetry.recent_errors(ERROR_LIST_SIZE),
            error_count_hour=telemetry.error_count(3600, now),
        )
        live.update(renderable, refresh=True)


async def setup(bot):
    await bot.add_cog(DashboardCog(bot))
