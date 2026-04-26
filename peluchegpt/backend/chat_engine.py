"""Pipeline híbrido entre modelo local e Gemini cloud."""

from __future__ import annotations

from datetime import datetime

from .classifier import classify
from .config import load_config
from .gemini_llm import generate as gemini_generate
from .local_llm import generate as local_generate
from .lore_indexer import lore_indexer


def build_system_prompt() -> str:
    """Gera o system prompt com data/hora atual e contexto fixo do P3LUCHE."""
    now = datetime.now()
    data_hora = now.strftime("%A, %d de %B de %Y — %H:%M")

    # Tradução dos dias e meses pro português
    dias = {
        "Monday": "Segunda-feira", "Tuesday": "Terça-feira", "Wednesday": "Quarta-feira",
        "Thursday": "Quinta-feira", "Friday": "Sexta-feira", "Saturday": "Sábado", "Sunday": "Domingo"
    }
    meses = {
        "January": "Janeiro", "February": "Fevereiro", "March": "Março", "April": "Abril",
        "May": "Maio", "June": "Junho", "July": "Julho", "August": "Agosto",
        "September": "Setembro", "October": "Outubro", "November": "Novembro", "December": "Dezembro"
    }
    for en, pt in {**dias, **meses}.items():
        data_hora = data_hora.replace(en, pt)

    return f"""Você é o PelucheGPT — assistente pessoal e exclusivo do dono do bot Discord P3LUCHE.

## IDENTIDADE
- Você é inteligente, direto e fala português brasileiro naturalmente
- Você tem acesso ao banco de dados de Lore, economia e logs do bot P3LUCHE
- Você roda localmente no PC do seu dono — é 100% privado
- Seja conciso quando a pergunta for simples, detalhado quando necessário

## DATA E HORA ATUAL
- Hoje é {data_hora}
- Fuso horário: Brasil (BRT, UTC-3)
- Responda perguntas sobre data/hora usando essa informação

## CONHECIMENTO DO SISTEMA P3LUCHE
- Bot Discord chamado P3LUCHE, desenvolvido em Python com discord.py
- Possui sistema de economia (Sachês, pesca, loja rotativa)
- Possui sistema de Lore/Roleplay para personagens de RPG
- Possui Jukebox integrado ao Google Drive para músicas
- Banco de dados: SQLite (bot.db)
- Hospedado localmente no PC do dono

## REGRAS
- NUNCA diga que não sabe a data ou hora — você tem acesso a ela acima
- Se não souber algo específico do Lore, diga que pode buscar no banco
- Não invente informações sobre personagens ou eventos do Lore
- Se a pergunta for sobre o sistema, responda com base no que sabe do P3LUCHE
- Responda SEMPRE em português brasileiro
"""


async def process_message(user_input: str, history: list) -> dict:
    """Processa mensagem com busca de contexto e roteamento inteligente."""
    cfg = load_config()
    top_k = max(1, int(cfg.max_context_chunks))

    # Busca contexto relevante no Lore
    context_chunks = await lore_indexer.search(user_input, top_k=top_k)

    # Classifica complexidade
    complexity = classify(user_input, context_chunks, threshold=cfg.complexity_threshold)

    # Monta system prompt com data atual
    system_prompt = build_system_prompt()

    if complexity == "simple":
        result = await local_generate(user_input, context_chunks, history, system_prompt=system_prompt)
        source = "local"
    else:
        result = await gemini_generate(user_input, context_chunks, history, system_prompt=system_prompt)
        source = "gemini"

    return {
        "response": result.get("text", ""),
        "source": source,
        "tokens_used": int(result.get("tokens_used", 0)),
        "context_used": context_chunks,
    }