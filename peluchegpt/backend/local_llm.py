"""Cliente do Ollama para respostas locais."""

from __future__ import annotations

import httpx

from .config import load_config


async def generate(
    user_input: str,
    context_chunks: list[dict],
    history: list[dict],
    system_prompt: str = "",
) -> dict:
    """Gera resposta local via endpoint HTTP do Ollama."""
    cfg = load_config()

    # Monta contexto de lore
    context_text = "\n\n".join(
        f"- {chunk.get('title', 'Sem título')}: {chunk.get('content', '')[:500]}"
        for chunk in context_chunks
    )

    # Monta histórico recente
    messages = []

    # System prompt com data/hora e identidade
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Adiciona contexto de lore ao system se existir
    if context_text:
        messages.append({
            "role": "system",
            "content": f"Contexto de Lore relevante para essa pergunta:\n{context_text}"
        })

    # Histórico da conversa
    for item in history[-8:]:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Pergunta atual
    messages.append({"role": "user", "content": user_input})

    payload = {
        "model": cfg.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": cfg.ollama_num_ctx or 2048,
            "temperature": 0.4,
            "num_gpu": cfg.ollama_num_gpu or 12,
            "num_thread": cfg.ollama_num_threads or 5,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{cfg.ollama_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = data.get("message", {}).get("content", "").strip()
    eval_count = data.get("eval_count", 0)
    prompt_eval_count = data.get("prompt_eval_count", 0)
    return {"text": text, "tokens_used": eval_count + prompt_eval_count}