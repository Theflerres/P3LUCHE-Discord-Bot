"""Classificador heurístico local (sem APIs externas)."""

from __future__ import annotations

import re


# Só manda pro Gemini se for REALMENTE complexo
COMPLEX_KEYWORDS = [
    "analise", "análise", "disserte", "argumente", "interprete",
    "filosof", "estratégia", "elabore", "explique detalhadamente",
    "faça um resumo completo", "compare e contraste",
    "quais são as implicações", "desenvolva", "escreva um texto",
    "redija", "crie uma história", "escreva uma história",
]

# Tudo isso fica LOCAL sempre
LOCAL_ALWAYS = [
    # Data e hora
    "que dia", "que horas", "hora atual", "data de hoje", "hoje é",
    "que horas são", "dia da semana", "que mês", "que ano",
    # Bot e sistema
    "saldo", "pesca", "pescaria", "música", "musica", "warn",
    "economia", "usuário", "usuario", "sachê", "sache", "loja",
    "jukebox", "drive", "bot", "servidor", "discord",
    # Perguntas factuais simples
    "quem é", "o que é", "quando foi", "onde fica", "qual é",
    "quantos", "quanto", "como se chama", "me fala sobre",
    "me conta sobre", "o que você sabe sobre",
    # Conversa casual
    "olá", "oi", "tudo bem", "obrigado", "valeu", "ok",
    "blz", "certo", "entendi", "legal", "show",
]


def classify(user_input: str, context_chunks: list[dict], threshold: float = 0.7) -> str:
    """Define se a pergunta vai para LOCAL ou GEMINI por heurísticas leves."""
    text = user_input.strip().lower()
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    word_count = len(words)

    # SEMPRE local — perguntas do dia a dia, bot, casual
    if any(k in text for k in LOCAL_ALWAYS):
        return "simple"

    # Mensagens curtas (até 8 palavras) sempre local
    if word_count <= 8:
        return "simple"

    # Só manda pro Gemini se tiver keyword explicitamente complexa
    if any(k in text for k in COMPLEX_KEYWORDS):
        return "complex"

    # Muitas entidades próprias = pode precisar de raciocínio mais pesado
    entities = len(re.findall(r"\b[A-ZÁÉÍÓÚÃÕÇ][a-záéíóúãõç]{2,}\b", user_input))
    if entities >= 4 and word_count >= 20:
        return "complex"

    # Por padrão, fica LOCAL — só usa Gemini quando realmente necessário
    return "simple"