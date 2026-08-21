"""Coleta de dados e composição visual do painel de status.

As funções `collect_*` são puras o suficiente para teste: recebem o processo
(psutil), a conexão do banco e o bot por parâmetro, e nunca levantam exceção —
devolvem `None` nos campos que falharam, e o render mostra "n/d". Painel
quebrado nunca pode derrubar o bot.

Custo por refresh:
  * a cada frame (~8 fps): uptime, latência — aritmética pura, custo desprezível
  * a cada 1s: CPU/RAM via psutil
  * a cada 45s: uma única query agregada de economia (3 subselects)
"""
from datetime import datetime, timedelta

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import telemetry
from cogs.dashboard_animation import apply_glitch
from cogs.dashboard_art import (
    DELTA_ART,
    SCREEN_HEIGHT,
    SCREEN_LEFT,
    SCREEN_WIDTH,
    SCREEN_TOP,
    TV_FRAME,
)

#: Intervalos de cache (segundos).
PROCESS_STATS_TTL = 1.0
ECONOMY_TTL = 45.0

#: Janela considerada "agora" para atividade de usuários.
ACTIVITY_WINDOW_SECONDS = 600

NORMAL_COLOR = "bright_green"
ERROR_COLOR = "bright_red"
DIM_NORMAL_COLOR = "green"
DIM_ERROR_COLOR = "red"


# ──────────────────────────────────────────────
#  FORMATAÇÃO
# ──────────────────────────────────────────────

def format_uptime(delta: timedelta) -> str:
    """Formata uptime como `2d 03:14:22` (dias só aparecem se houver)."""
    total = int(max(delta.total_seconds(), 0))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    base = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {base}" if days else base


def format_number(value) -> str:
    """Milhar com ponto, no padrão pt-BR."""
    if value is None:
        return "n/d"
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "n/d"


# ──────────────────────────────────────────────
#  COLETORES
# ──────────────────────────────────────────────

def collect_connection(bot, now: datetime = None, process_start: datetime = None) -> dict:
    """Uptime, latência do gateway e status de conexão."""
    now = now or datetime.now()
    start = getattr(bot, "start_time", None) or process_start or now

    latency_ms = None
    try:
        raw = bot.latency
        # discord.py devolve nan antes do primeiro heartbeat.
        if raw is not None and raw == raw and raw not in (float("inf"), float("-inf")):
            latency_ms = round(raw * 1000)
    except Exception:
        pass

    try:
        ready = bool(bot.is_ready()) and not bool(bot.is_closed())
    except Exception:
        ready = False

    return {
        "uptime": now - start,
        "latency_ms": latency_ms,
        "connected": ready,
    }


def collect_process_stats(process) -> dict:
    """CPU e RAM do próprio processo do bot (via psutil.Process)."""
    stats = {"cpu_percent": None, "ram_mb": None, "ram_percent": None}
    try:
        stats["cpu_percent"] = process.cpu_percent(None)
    except Exception:
        pass
    try:
        stats["ram_mb"] = process.memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        stats["ram_percent"] = process.memory_percent()
    except Exception:
        pass
    return stats


def collect_economy(conn, now: datetime = None) -> dict:
    """Jogadores registrados, ativos hoje e Sachês em circulação.

    Uma query só, com três subselects agregados sobre as tabelas v4. Chamada no
    máximo a cada `ECONOMY_TTL` segundos — nunca a cada frame.
    """
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    empty = {"total_players": None, "active_today": None, "total_sachets": None}
    if conn is None:
        return empty
    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS total_players,
                (SELECT COALESCE(SUM(wallet), 0) FROM users) AS total_sachets,
                (SELECT COUNT(*) FROM user_cooldowns
                   WHERE COALESCE(last_fish, '')    >= ?
                      OR COALESCE(last_daily, '')   >= ?
                      OR COALESCE(last_explore, '') >= ?) AS active_today
            """,
            (day, day, day),
        ).fetchone()
    except Exception:
        return empty
    if row is None:
        return empty
    return {
        "total_players": row["total_players"],
        "active_today": row["active_today"],
        "total_sachets": row["total_sachets"],
    }


class LatencyTracker:
    """Acompanha `bot.latency` e desde quando aquele número está parado.

    Existe por causa de uma confusão real: o gateway manda heartbeat a cada
    ~41s, então `bot.latency` é um valor ÚNICO por batida que fica congelado no
    intervalo inteiro. Mostrar só o número faz parecer uma medida contínua
    "travada" num valor ruim — e a primeira batida ainda por cima é medida
    durante o startup do bot, quando o loop está ocupado, saindo mais alta que
    o regime. Exibir a idade da leitura e o mínimo observado deixa isso óbvio.
    """

    #: Quantas leituras distintas guardar para calcular o mínimo observado.
    HISTORY = 20

    def __init__(self):
        self.value = None
        self.measured_at = None
        self._history = []

    def update(self, latency_ms, now: datetime) -> None:
        """Alimenta o rastreador. Só conta como leitura nova quando o valor muda."""
        if latency_ms is None:
            return
        if latency_ms != self.value:
            self.value = latency_ms
            self.measured_at = now
            self._history.append(latency_ms)
            del self._history[: -self.HISTORY]

    def age_seconds(self, now: datetime):
        if self.measured_at is None:
            return None
        return max((now - self.measured_at).total_seconds(), 0.0)

    @property
    def observed_min(self):
        return min(self._history) if self._history else None


class DashboardData:
    """Agrega os coletores respeitando os TTLs de cada um."""

    def __init__(self, process=None, process_start: datetime = None):
        self.process = process
        self.process_start = process_start or datetime.now()
        self.latency = LatencyTracker()
        self._process_cache = None
        self._process_cache_at = None
        self._economy_cache = None
        self._economy_cache_at = None

    def process_stats(self, now: datetime = None) -> dict:
        now = now or datetime.now()
        expired = (
            self._process_cache_at is None
            or (now - self._process_cache_at).total_seconds() >= PROCESS_STATS_TTL
        )
        if expired:
            if self.process is None:
                self._process_cache = {"cpu_percent": None, "ram_mb": None, "ram_percent": None}
            else:
                self._process_cache = collect_process_stats(self.process)
            self._process_cache_at = now
        return self._process_cache

    def economy(self, conn, now: datetime = None) -> dict:
        now = now or datetime.now()
        expired = (
            self._economy_cache_at is None
            or (now - self._economy_cache_at).total_seconds() >= ECONOMY_TTL
        )
        if expired:
            self._economy_cache = collect_economy(conn, now)
            self._economy_cache_at = now
        return self._economy_cache


# ──────────────────────────────────────────────
#  RENDER — TV
# ──────────────────────────────────────────────

def should_redraw(has_glitch: bool, was_glitching: bool, current_second, last_second) -> bool:
    """Decide se vale redesenhar o painel neste frame.

    O loop roda a 8 fps por causa do glitch, mas em repouso o desenho só muda
    quando o relógio do uptime vira — redesenhar 8x por segundo ali seria gastar
    CPU para reimprimir pixel por pixel a mesma coisa. Então:

      * glitch acontecendo  -> redesenha (cada frame de glitch é diferente)
      * glitch acabou agora -> redesenha uma vez, para restaurar a imagem limpa
      * senão               -> só quando muda o segundo (1 fps)
    """
    if has_glitch or was_glitching:
        return True
    return current_second != last_second


def render_tv_lines(frame, rng) -> list:
    """Compõe a moldura estática com o conteúdo animado da tela."""
    screen = apply_glitch(list(DELTA_ART), frame.glitch_type, rng, frame.intensity)
    lines = []
    for index, frame_line in enumerate(TV_FRAME):
        screen_row = index - SCREEN_TOP
        if 0 <= screen_row < SCREEN_HEIGHT and screen_row < len(screen):
            content = screen[screen_row][:SCREEN_WIDTH].ljust(SCREEN_WIDTH)
            lines.append(
                frame_line[:SCREEN_LEFT] + content + frame_line[SCREEN_LEFT + SCREEN_WIDTH:]
            )
        else:
            lines.append(frame_line)
    return lines


def build_tv(frame, rng) -> Text:
    """A TV como renderable colorido — verde no normal, vermelho no erro."""
    bright = ERROR_COLOR if frame.is_error else NORMAL_COLOR
    dim = DIM_ERROR_COLOR if frame.is_error else DIM_NORMAL_COLOR
    text = Text()
    for index, line in enumerate(render_tv_lines(frame, rng)):
        screen_row = index - SCREEN_TOP
        on_screen = 0 <= screen_row < SCREEN_HEIGHT
        text.append(line + "\n", style=f"bold {bright}" if on_screen else dim)
    return text


# ──────────────────────────────────────────────
#  RENDER — SEÇÕES DE DADOS
# ──────────────────────────────────────────────

def _section(title: str, rows: list, accent: str) -> Group:
    """Título fora da grid de propósito: dentro dela, ele alargaria a primeira
    coluna e empurraria todos os valores da seção para a direita."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(no_wrap=True)
    for label, value in rows:
        table.add_row(label, value)
    return Group(Text(title, style=f"bold {accent}"), table)


def format_gateway_latency(tracker: "LatencyTracker", now: datetime) -> Text:
    """Latência do gateway deixando explícito que é uma amostra periódica.

    Formato: `137 ms · há 12s · mín 133`. O "há Xs" é o antídoto para achar que
    o número está travado: ele só muda quando chega um heartbeat novo (~41s).
    """
    if tracker.value is None:
        return Text("aguardando 1º heartbeat…", style="dim")

    text = Text(f"{tracker.value} ms")
    age = tracker.age_seconds(now)
    if age is not None:
        text.append(f" · há {int(age)}s", style="dim")
    if tracker.observed_min is not None and tracker.observed_min != tracker.value:
        text.append(f" · mín {tracker.observed_min}", style="dim")
    return text


def build_connection_section(connection: dict, accent: str, latency_tracker=None,
                             now: datetime = None) -> Group:
    status = (
        Text("● conectado", style="bright_green")
        if connection.get("connected")
        else Text("● reconectando", style="bright_yellow")
    )

    if latency_tracker is not None:
        gateway = format_gateway_latency(latency_tracker, now or datetime.now())
    else:
        latency = connection.get("latency_ms")
        gateway = Text(f"{latency} ms" if latency is not None else "n/d")

    return _section(
        "CONEXÃO",
        [
            ("uptime", format_uptime(connection.get("uptime") or timedelta())),
            ("gateway", gateway),
            ("status", status),
        ],
        accent,
    )


def build_process_section(stats: dict, accent: str) -> Group:
    cpu = stats.get("cpu_percent")
    ram_mb = stats.get("ram_mb")
    ram_pct = stats.get("ram_percent")
    ram_text = "n/d"
    if ram_mb is not None:
        ram_text = f"{ram_mb:.0f} MB"
        if ram_pct is not None:
            ram_text += f" ({ram_pct:.1f}%)"
    return _section(
        "PROCESSO",
        [
            ("cpu", f"{cpu:.1f}%" if cpu is not None else "n/d"),
            ("ram", ram_text),
        ],
        accent,
    )


def build_economy_section(economy: dict, accent: str) -> Group:
    return _section(
        "ECONOMIA",
        [
            ("jogadores", format_number(economy.get("total_players"))),
            ("ativos hoje", format_number(economy.get("active_today"))),
            ("sachês", format_number(economy.get("total_sachets"))),
        ],
        accent,
    )


def build_activity_section(interactions: list, accent: str, window_seconds: int) -> Group:
    minutes = window_seconds // 60
    if not interactions:
        rows = [("—", Text("ninguém por agora", style="dim"))]
    else:
        rows = []
        for entry in interactions:
            when = entry["when"].strftime("%H:%M")
            rows.append((when, f"{entry['user_name']} · /{entry['command_name']}"))
    return _section(f"ATIVIDADE (últimos {minutes} min)", rows, accent)


def build_errors_section(errors: list, count_hour: int, accent: str) -> Group:
    if not errors:
        rows = [("—", Text("nenhum erro recente", style="dim"))]
    else:
        rows = []
        for entry in errors:
            when = entry["when"].strftime("%H:%M")
            style = "bright_red" if entry["level"] in telemetry.ERROR_LEVELS else "yellow"
            message = entry["message"]
            if len(message) > 46:
                message = message[:45] + "…"
            rows.append((when, Text(message, style=style)))
    title = f"ERROS (última hora: {count_hour})"
    return _section(title, rows, accent)


def build_dashboard(frame, rng, connection, stats, economy, interactions, errors,
                    error_count_hour, latency_tracker=None, now: datetime = None):
    """Monta o painel completo: TV à esquerda, dados à direita."""
    accent = ERROR_COLOR if frame.is_error else NORMAL_COLOR

    info = Group(
        build_connection_section(connection, accent, latency_tracker, now),
        Text(""),
        build_process_section(stats, accent),
        Text(""),
        build_economy_section(economy, accent),
        Text(""),
        build_activity_section(interactions, accent, ACTIVITY_WINDOW_SECONDS),
        Text(""),
        build_errors_section(errors, error_count_hour, accent),
    )

    layout = Table.grid(padding=(0, 3))
    layout.add_column(no_wrap=True)
    layout.add_column()
    layout.add_row(build_tv(frame, rng), info)

    return Panel(
        layout,
        title=Text("P3LUCHE · painel de status", style=f"bold {accent}"),
        border_style=accent,
        padding=(0, 1),
    )
