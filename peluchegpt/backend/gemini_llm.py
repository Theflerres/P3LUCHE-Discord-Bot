"""Cliente Gemini para respostas complexas."""

from __future__ import annotations

from google import genai
from google.genai import types

from .config import load_config


async def generate(
    user_input: str,
    context_chunks: list[dict],
    history: list[dict],
    system_prompt: str = "",
) -> dict:
    """Executa inferência no Gemini quando necessário."""
    cfg = load_config()
    if not cfg.gemini_api_key:
        return {"text": "Chave Gemini não configurada.", "tokens_used": 0}

    # Monta contexto de lore
    context_text = "\n\n".join(
        f"[{idx + 1}] {chunk.get('title', 'Sem título')}:\n{chunk.get('content', '')[:1200]}"
        for idx, chunk in enumerate(context_chunks)
    )

    # Monta histórico da conversa
    history_text = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in history[-12:]
    )

    # Prompt completo combinando system + contexto + histórico + pergunta
    full_prompt = (
        f"{system_prompt}\n\n"
        f"{'Contexto de Lore relevante:' + chr(10) + context_text + chr(10) + chr(10) if context_text else ''}"
        f"{'Histórico da conversa:' + chr(10) + history_text + chr(10) + chr(10) if history_text else ''}"
        f"Pergunta atual: {user_input}"
    )

    client = genai.Client(api_key=cfg.gemini_api_key)

    response = await client.aio.models.generate_content(
        model="gemini-1.5-pro",
        contents=full_prompt,
    )

    tokens = 0
    if response.usage_metadata:
        tokens = int(
            (response.usage_metadata.prompt_token_count or 0)
            + (response.usage_metadata.candidates_token_count or 0)
        )

    text = (response.text or "").strip()
    return {"text": text, "tokens_used": tokens}