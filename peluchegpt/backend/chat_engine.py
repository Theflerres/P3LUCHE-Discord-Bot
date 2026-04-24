"""Pipeline híbrido entre Mistral local e Gemini cloud."""

from __future__ import annotations

from .classifier import classify
from .config import load_config
from .gemini_llm import generate as gemini_generate
from .local_llm import generate as local_generate
from .lore_indexer import lore_indexer


async def process_message(user_input: str, history: list) -> dict:
    """Processa mensagem com busca de contexto e roteamento inteligente."""
    cfg = load_config()
    top_k = max(1, int(cfg.max_context_chunks))
    context_chunks = await lore_indexer.search(user_input, top_k=top_k)
    complexity = classify(user_input, context_chunks, threshold=cfg.complexity_threshold)

    if complexity == "simple":
        result = await local_generate(user_input, context_chunks, history)
        source = "local"
    else:
        result = await gemini_generate(user_input, context_chunks, history)
        source = "gemini"

    return {
        "response": result.get("text", ""),
        "source": source,
        "tokens_used": int(result.get("tokens_used", 0)),
        "context_used": context_chunks,
    }
