"""Classificador heurístico local (sem APIs externas)."""

from __future__ import annotations

import re


ADVANCED_KEYWORDS = [
    "por que",
    "analise",
    "análise",
    "compare",
    "comparar",
    "escreva",
    "disserte",
    "argumente",
    "interprete",
    "filosof",
    "estratégia",
]

LORE_SIMPLE_PATTERNS = [
    r"^quem\s+é",
    r"^o\s+que\s+é",
    r"^quando\s+foi",
    r"^onde\s+fica",
]

BOT_DATA_KEYWORDS = [
    "saldo",
    "pesca",
    "pescaria",
    "música",
    "musica",
    "warn",
    "economia",
    "usuário",
    "usuario",
]


def classify(user_input: str, context_chunks: list[dict], threshold: float = 0.7) -> str:
    """Define se a pergunta vai para LOCAL ou GEMINI por heurísticas leves."""
    text = user_input.strip().lower()
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    word_count = len(words)

    # Perguntas sobre dados diretos do bot ficam no fluxo local/SQLite.
    if any(k in text for k in BOT_DATA_KEYWORDS):
        return "simple"

    # Perguntas explicitamente analíticas sobem para modelo cloud.
    if any(k in text for k in ADVANCED_KEYWORDS):
        return "complex"

    # Perguntas curtas factuais de lore tendem a ser simples.
    if any(re.search(p, text) for p in LORE_SIMPLE_PATTERNS) and word_count <= 12:
        return "simple"

    # Múltiplos personagens/entidades citados sugerem raciocínio mais pesado.
    entities_hint = len(re.findall(r"\b[A-ZÁÉÍÓÚÃÕÇ][a-záéíóúãõç]+\b", user_input))
    if entities_hint >= 3:
        return "complex"

    # Score simples híbrido com contexto recuperado.
    context_confidence = min(len(context_chunks) / 5, 1.0)
    complexity_score = (word_count / 30) * 0.6 + (1 - context_confidence) * 0.4

    return "complex" if complexity_score >= threshold else "simple"
