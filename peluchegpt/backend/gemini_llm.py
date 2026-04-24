"""Cliente Gemini para respostas complexas."""

from __future__ import annotations

from google import genai
from google.genai import types

from .config import load_config


def _build_prompt(user_input: str, context_chunks: list[dict], history: list[dict]) -> str:
    """Prepara contexto combinado para perguntas avançadas."""
    context_text = "\n\n".join(
        f"[{idx + 1}] {chunk.get('title', 'Sem título')}:\n{chunk.get('content', '')[:1200]}"
        for idx, chunk in enumerate(context_chunks)
    )
    history_text = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-12:]
    )
    return (
        "Você é o PelucheGPT, assistente do P3LUCHE.\n"
        "Responda em português com clareza e precisão.\n"
        "Se faltar contexto, deixe explícitas as limitações.\n"
        f"\nHistórico:\n{history_text or 'Sem histórico'}\n"
        f"\nContexto recuperado:\n{context_text or 'Sem contexto'}\n"
        f"\nPergunta:\n{user_input}"
    )


async def generate(user_input: str, context_chunks: list[dict], history: list[dict]) -> dict:
    """Executa inferência no Gemini 1.5 Pro quando necessário."""
    cfg = load_config()
    if not cfg.gemini_api_key:
        return {"text": "Chave Gemini não configurada.", "tokens_used": 0}

    client = genai.Client(api_key=cfg.gemini_api_key)
    prompt = _build_prompt(user_input, context_chunks, history)

    response = await client.aio.models.generate_content(
        model="gemini-1.5-pro",
        contents=prompt,
    )

    tokens = 0
    if response.usage_metadata:
        tokens = int(
            (response.usage_metadata.prompt_token_count or 0)
            + (response.usage_metadata.candidates_token_count or 0)
        )

    text = (response.text or "").strip()
    return {"text": text, "tokens_used": tokens}