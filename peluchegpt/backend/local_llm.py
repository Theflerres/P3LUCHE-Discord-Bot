"""Cliente do Ollama para respostas locais com Mistral."""

from __future__ import annotations

import httpx

from .config import load_config


def _build_prompt(user_input: str, context_chunks: list[dict], history: list[dict]) -> str:
    """Monta um prompt enxuto para reduzir latência e consumo de RAM."""
    context_text = "\n\n".join(
        f"- {chunk.get('title', 'Sem título')}: {chunk.get('content', '')[:500]}"
        for chunk in context_chunks
    )
    history_text = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-8:]
    )

    return (
        "Você é o PelucheGPT, assistente pessoal do P3LUCHE.\n"
        "Responda em português e seja objetivo.\n"
        f"\nContexto de lore:\n{context_text or 'Sem contexto relevante.'}\n"
        f"\nHistórico recente:\n{history_text or 'Sem histórico.'}\n"
        f"\nPergunta atual:\n{user_input}"
    )


async def generate(user_input: str, context_chunks: list[dict], history: list[dict]) -> dict:
    """Gera resposta local via endpoint HTTP do Ollama."""
    cfg = load_config()
    prompt = _build_prompt(user_input, context_chunks, history)

    payload = {
        "model": cfg.ollama_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            # Limita uso para não dominar os recursos da máquina.
            "num_ctx": 2048,
            "temperature": 0.4,
            "num_gpu": 1,
        },
    }

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(f"{cfg.ollama_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = data.get("message", {}).get("content", "").strip()
    eval_count = data.get("eval_count", 0)
    prompt_eval_count = data.get("prompt_eval_count", 0)
    return {"text": text, "tokens_used": eval_count + prompt_eval_count}
