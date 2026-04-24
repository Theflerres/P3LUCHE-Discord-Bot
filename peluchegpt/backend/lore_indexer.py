"""Indexação e busca semântica de lore com ChromaDB."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from .config import CHROMA_DIR, INDEX_META_PATH, load_config
from .db import get_lore_entries


class LoreIndexer:
    """Gerencia embeddings, sincronização e consultas vetoriais."""

    def __init__(self) -> None:
        self._client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self._collection = self._client.get_or_create_collection("peluche_lore")
        self._embedder: SentenceTransformer | None = None
        self._lock = asyncio.Lock()
        self._last_indexed_at: str | None = None
        self._last_hash: str | None = None
        self._load_meta()

    def _load_embedder(self) -> SentenceTransformer:
        """Carrega embedding model sob demanda para reduzir boot inicial."""
        if self._embedder is None:
            self._embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        return self._embedder

    def _sqlite_hash(self) -> str:
        """Calcula hash do arquivo SQLite para detectar mudanças."""
        db_path = Path(load_config().sqlite_path)
        if not db_path.exists():
            return "missing"
        digest = hashlib.sha256()
        stat = db_path.stat()
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
        return digest.hexdigest()

    def _load_meta(self) -> None:
        """Recupera metadados da última indexação."""
        if not INDEX_META_PATH.exists():
            return
        data = json.loads(INDEX_META_PATH.read_text(encoding="utf-8"))
        self._last_hash = data.get("sqlite_hash")
        self._last_indexed_at = data.get("last_indexed_at")

    def _save_meta(self, sqlite_hash: str) -> None:
        """Salva estado do índice para reindexação incremental."""
        self._last_hash = sqlite_hash
        self._last_indexed_at = datetime.now(timezone.utc).isoformat()
        INDEX_META_PATH.write_text(
            json.dumps(
                {"sqlite_hash": sqlite_hash, "last_indexed_at": self._last_indexed_at},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def ensure_indexed(self, force: bool = False) -> dict[str, Any]:
        """Reindexa automaticamente quando detectar alteração no SQLite."""
        async with self._lock:
            sqlite_hash = self._sqlite_hash()
            if not force and sqlite_hash == self._last_hash:
                return {"reindexed": False, "reason": "no_changes"}

            # Remove coleção para evitar resíduos de versões antigas.
            self._client.delete_collection("peluche_lore")
            self._collection = self._client.get_or_create_collection("peluche_lore")

            rows = get_lore_entries()
            if not rows:
                self._save_meta(sqlite_hash)
                return {"reindexed": True, "entries": 0}

            embedder = self._load_embedder()
            docs, metadatas, ids = [], [], []
            for row in rows:
                rid = str(row.get("id", len(ids)))
                title = str(row.get("title", "Sem título"))
                content = str(row.get("content", ""))
                tags = str(row.get("tags", ""))
                docs.append(f"{title}\n{content}\nTags: {tags}")
                metadatas.append({"title": title, "tags": tags})
                ids.append(rid)

            embeddings = embedder.encode(docs, normalize_embeddings=True).tolist()
            self._collection.add(documents=docs, metadatas=metadatas, ids=ids, embeddings=embeddings)
            self._save_meta(sqlite_hash)
            return {"reindexed": True, "entries": len(ids)}

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Executa busca vetorial para recuperar contexto relevante."""
        await self.ensure_indexed(force=False)
        if not query.strip():
            return []

        embedder = self._load_embedder()
        emb = embedder.encode([query], normalize_embeddings=True).tolist()[0]
        result = self._collection.query(query_embeddings=[emb], n_results=top_k)

        chunks: list[dict[str, Any]] = []
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for i, doc in enumerate(documents):
            chunks.append(
                {
                    "id": ids[i] if i < len(ids) else None,
                    "title": (metadatas[i] or {}).get("title", "Sem título") if i < len(metadatas) else "Sem título",
                    "content": doc,
                    "distance": distances[i] if i < len(distances) else None,
                }
            )
        return chunks

    def stats(self) -> dict[str, Any]:
        """Retorna estatísticas básicas para monitoramento na UI."""
        count = self._collection.count()
        size_on_disk = 0
        if CHROMA_DIR.exists():
            size_on_disk = sum(p.stat().st_size for p in CHROMA_DIR.rglob("*") if p.is_file())
        return {
            "entries_indexed": count,
            "last_indexed_at": self._last_indexed_at,
            "index_size_bytes": size_on_disk,
        }


lore_indexer = LoreIndexer()
