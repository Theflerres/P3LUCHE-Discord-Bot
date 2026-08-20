"""Lógica de animação da TV do painel — pura, sem rich e sem I/O.

Separada do desenho (`dashboard_art.py`) e do render (`dashboard_panel.py`) para
ser testável de forma determinística: o gerador aleatório é injetável.

Dois estados:

  * NORMAL — Delta verde, glitch raro e imprevisível (~5% por segundo).
  * ERROR  — tudo vermelho, glitch bem mais frequente e mais intenso. Entra
    quando um erro é registrado e volta sozinho ao normal depois de
    `error_hold_seconds` sem erros novos (não some na hora, para você não perder
    a informação de que algo aconteceu; não fica preso em vermelho para sempre).

A probabilidade é definida *por segundo* e convertida para por-frame, então
mudar o FPS não muda a frequência percebida do glitch.
"""
import random
from dataclasses import dataclass

from cogs.dashboard_art import SCRAMBLE_CHARS, STATIC_CHARS

NORMAL_STATE = "normal"
ERROR_STATE = "error"

GLITCH_SCRAMBLE = "scramble"
GLITCH_TEARING = "tearing"
GLITCH_STATIC = "static"
GLITCH_JITTER = "jitter"

#: Tipos sorteados a cada glitch, para não ficar repetitivo.
GLITCH_TYPES = (GLITCH_SCRAMBLE, GLITCH_TEARING, GLITCH_STATIC, GLITCH_JITTER)

DEFAULT_FPS = 8
DEFAULT_ERROR_HOLD_SECONDS = 60.0
DEFAULT_NORMAL_GLITCH_PER_SECOND = 0.05
DEFAULT_ERROR_GLITCH_PER_SECOND = 0.55


@dataclass(frozen=True)
class Frame:
    """O que desenhar neste frame."""

    state: str
    glitch_type: str = None
    intensity: float = 1.0

    @property
    def is_error(self) -> bool:
        return self.state == ERROR_STATE

    @property
    def has_glitch(self) -> bool:
        return self.glitch_type is not None


def per_frame_probability(per_second: float, fps: int) -> float:
    """Converte probabilidade por segundo em probabilidade por frame."""
    if fps <= 0:
        return 0.0
    per_second = min(max(per_second, 0.0), 1.0)
    return 1.0 - (1.0 - per_second) ** (1.0 / fps)


class TVAnimation:
    """Máquina de estado da TV. `tick()` decide o frame; não desenha nada."""

    def __init__(
        self,
        fps: int = DEFAULT_FPS,
        error_hold_seconds: float = DEFAULT_ERROR_HOLD_SECONDS,
        normal_glitch_per_second: float = DEFAULT_NORMAL_GLITCH_PER_SECOND,
        error_glitch_per_second: float = DEFAULT_ERROR_GLITCH_PER_SECOND,
        rng: random.Random = None,
    ):
        self.fps = fps
        self.error_hold_seconds = error_hold_seconds
        self.rng = rng or random.Random()
        self._p_normal = per_frame_probability(normal_glitch_per_second, fps)
        self._p_error = per_frame_probability(error_glitch_per_second, fps)
        self._last_error_at = None
        self._glitch_frames_left = 0
        self._glitch_type = None

    # ── Estado ──

    def notify_error(self, when: float) -> None:
        """Marca que um erro aconteceu em `when` (timestamp monotônico/epoch)."""
        if self._last_error_at is None or when > self._last_error_at:
            self._last_error_at = when

    def state(self, now: float) -> str:
        if self._last_error_at is None:
            return NORMAL_STATE
        if now - self._last_error_at < self.error_hold_seconds:
            return ERROR_STATE
        return NORMAL_STATE

    # ── Frames ──

    def tick(self, now: float) -> Frame:
        """Avança um frame e devolve o que deve ser desenhado."""
        state = self.state(now)
        is_error = state == ERROR_STATE
        intensity = 2.0 if is_error else 1.0

        # Glitch em andamento ocupa 1-2 frames.
        if self._glitch_frames_left > 0:
            self._glitch_frames_left -= 1
            return Frame(state, self._glitch_type, intensity)

        probability = self._p_error if is_error else self._p_normal
        if self.rng.random() < probability:
            self._glitch_type = self.rng.choice(GLITCH_TYPES)
            # Dura 1 ou 2 frames; este já é o primeiro.
            self._glitch_frames_left = self.rng.randint(1, 2) - 1
            return Frame(state, self._glitch_type, intensity)

        self._glitch_type = None
        return Frame(state, None, intensity)


# ──────────────────────────────────────────────
#  TRANSFORMAÇÕES DE GLITCH (puras)
# ──────────────────────────────────────────────

def _shift(row: str, offset: int, width: int) -> str:
    """Desloca a linha horizontalmente, preservando a largura."""
    if offset == 0:
        return row
    if offset > 0:
        shifted = " " * offset + row
    else:
        shifted = row[-offset:]
    return shifted[:width].ljust(width)


def apply_glitch(rows: list, glitch_type: str, rng: random.Random, intensity: float = 1.0) -> list:
    """Aplica o glitch às linhas da tela, preservando dimensões.

    Devolve sempre uma lista nova com a mesma quantidade de linhas e a mesma
    largura por linha — o render depende disso para encaixar na moldura.
    """
    if not rows:
        return []
    width = len(rows[0])
    if glitch_type is None:
        return list(rows)

    if glitch_type == GLITCH_JITTER:
        # Tela inteira treme junto.
        offset = rng.choice([-2, -1, 1, 2]) if intensity > 1.0 else rng.choice([-1, 1])
        return [_shift(r, offset, width) for r in rows]

    if glitch_type == GLITCH_TEARING:
        # Tearing clássico de CRT: algumas linhas deslocadas individualmente.
        out = []
        chance = min(0.35 * intensity, 0.9)
        max_offset = 3 if intensity > 1.0 else 2
        for row in rows:
            if rng.random() < chance:
                offset = rng.randint(-max_offset, max_offset)
                out.append(_shift(row, offset, width))
            else:
                out.append(row)
        return out

    if glitch_type == GLITCH_STATIC:
        # Chuvisco substituindo parte da tela.
        chance = min(0.18 * intensity, 0.8)
        out = []
        for row in rows:
            chars = list(row)
            for i in range(len(chars)):
                if rng.random() < chance:
                    chars[i] = rng.choice(STATIC_CHARS)
            out.append("".join(chars))
        return out

    if glitch_type == GLITCH_SCRAMBLE:
        # Caracteres do desenho embaralhados/trocados por blocos.
        chance = min(0.30 * intensity, 0.85)
        out = []
        for row in rows:
            chars = list(row)
            for i, ch in enumerate(chars):
                if ch != " " and rng.random() < chance:
                    chars[i] = rng.choice(SCRAMBLE_CHARS)
            out.append("".join(chars))
        return out

    return list(rows)
