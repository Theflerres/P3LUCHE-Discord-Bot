"""Arte ASCII do painel de status — só desenho, nenhuma lógica.

Este módulo existe separado de `dashboard_animation.py` de propósito: dá para
redesenhar a TV aqui sem encostar no código de render/animação.

Contrato que o render assume (garantido por `tests/test_dashboard.py`):

  * Toda linha de `TV_FRAME` tem exatamente `FRAME_WIDTH` caracteres.
  * A "tela" da TV é o retângulo que começa na linha `SCREEN_TOP`, coluna
    `SCREEN_LEFT`, com `SCREEN_WIDTH` x `SCREEN_HEIGHT`.
  * Toda linha de `DELTA_ART` tem exatamente `SCREEN_WIDTH` caracteres, e são
    `SCREEN_HEIGHT` linhas.

Se você mexer no desenho, mantenha essas invariantes (ou ajuste as constantes
junto) — os testes falham na hora se a conta não fechar.
"""

FRAME_WIDTH = 25

#: Moldura estática da TV. As linhas de tela são preenchidas em runtime.
TV_FRAME = [
    "       \\    |    /       ",
    "        \\   |   /        ",
    "         \\  |  /         ",
    "          \\ | /          ",
    "  ╔═══════════════════╗  ",
    "  ║ ┌───────────────┐ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ │               │ ║  ",
    "  ║ └───────────────┘ ║  ",
    "  ║  ◉  ▭▭▭▭▭   ○ ○   ║  ",
    "  ╚═══════════════════╝  ",
    "     ╱▔▔▔▔▔▔▔▔▔▔▔▔▔╲     ",
    "    ╱_______________╲    ",
]

#: Canto superior-esquerdo da área útil da tela, dentro de TV_FRAME.
SCREEN_TOP = 6
SCREEN_LEFT = 5
SCREEN_WIDTH = 15
SCREEN_HEIGHT = 7

#: O Delta desenhado na tela (SCREEN_HEIGHT linhas de SCREEN_WIDTH colunas).
DELTA_ART = [
    "       █       ",
    "      █ █      ",
    "     █   █     ",
    "    █     █    ",
    "   █       █   ",
    "  █         █  ",
    " █████████████ ",
]

#: Caracteres usados para simular chuvisco/estática de tubo.
STATIC_CHARS = "░▒▓·:*#%@!?/\\|-_=+~^"

#: Caracteres usados no embaralhamento (glitch "scramble").
SCRAMBLE_CHARS = "▚▞▛▜▙▟▄▀█▌▐"
