"""
vectorstore.py — FAISS persistente con actualización incremental.

Capa de abstracción VectorStore lista para sustituir FAISS por
Pinecone, Weaviate o cualquier otro vector DB sin cambiar el resto del sistema.

Estrategia de persistencia:
  - Índice FAISS binario:   data/faiss.index
  - Metadatos paralelos:    data/faiss_metadata.json

Actualización incremental: index.add() añade al índice existente;
no reconstruye desde cero.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from rag.config import EMBEDDING_DIM, FAISS_INDEX_PATH, FAISS_METADATA_PATH
from rag.embeddings import EmbeddingProvider


class VectorStore(ABC):
    """
    Interfaz abstracta para vector stores.
    Subclasear para implementar Pinecone, Weaviate, Qdrant, etc.
    """

    @abstractmethod
    def add_documents(self, chunks: list[dict[str, Any]]) -> None:
        """Añadir chunks al índice. Operación incremental."""
        ...

    @abstractmethod
    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        """Buscar por similitud. Devuelve chunks con campo 'score' añadido."""
        ...

    @abstractmethod
    def save(self) -> None:
        """Persistir el índice y los metadatos en disco."""
        ...

    @abstractmethod
    def load(self) -> bool:
        """Cargar desde disco. Devuelve True si tuvo éxito."""
        ...

    @abstractmethod
    def is_empty(self) -> bool:
        ...

    @property
    @abstractmethod
    def total_vectors(self) -> int:
        """Número de vectores en el índice."""
        ...


class FAISSVectorStore(VectorStore):
    """
    Vector store FAISS con persistencia y actualización incremental.

    Usa IndexFlatIP (producto interior) sobre vectores L2-normalizados
    → equivalente a similitud coseno, O(n) exacto, sin aproximación.

    Para conjuntos > 1M vectores considerar IndexIVFFlat o HNSW.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        index_path: Path = FAISS_INDEX_PATH,
        metadata_path: Path = FAISS_METADATA_PATH,
        dimension: int = EMBEDDING_DIM,
    ) -> None:
        self._embedder = embedding_provider
        self._index_path = Path(index_path)
        self._metadata_path = Path(metadata_path)
        self._dim = dimension
        self._metadata: list[dict[str, Any]] = []
        self._index: faiss.Index = faiss.IndexFlatIP(dimension)

        # Intentar cargar índice existente en disco
        self.load()

    # ── VectorStore interface ─────────────────────────────────────────────────

    def add_documents(self, chunks: list[dict[str, Any]]) -> None:
        """
        Añade chunks al índice incrementalmente.
        Llama al proveedor de embeddings para vectorizar los textos.

        Args:
            chunks: lista de dicts con al menos {"text": str, "source": str, ...}
        """
        if not chunks:
            return

        # Deduplicar: saltar chunks con doc_hash ya presente en metadatos
        existing_hashes = {m.get("doc_hash") for m in self._metadata if m.get("doc_hash")}
        new_chunks = [
            c for c in chunks
            if not c.get("doc_hash") or c["doc_hash"] not in existing_hashes
        ]
        if not new_chunks:
            return

        texts = [c["text"] for c in new_chunks]
        vectors = self._embedder.embed_texts(texts)
        matrix = np.array(vectors, dtype=np.float32)

        self._index.add(matrix)
        self._metadata.extend(new_chunks)
        self.save()

    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        """
        Búsqueda por similitud coseno.
        Devuelve hasta top_k resultados con campo 'score' (0-1).
        """
        if self.is_empty():
            return []

        query = np.array([query_vector], dtype=np.float32)
        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = dict(self._metadata[idx])
            chunk["score"] = float(score)
            results.append(chunk)

        return results

    def save(self) -> None:
        """Persiste el índice FAISS y los metadatos en disco."""
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._metadata_path.write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> bool:
        """Carga el índice y metadatos desde disco si existen."""
        if not self._index_path.exists() or not self._metadata_path.exists():
            return False
        try:
            self._index = faiss.read_index(str(self._index_path))
            self._metadata = json.loads(
                self._metadata_path.read_text(encoding="utf-8")
            )
            return True
        except Exception:
            self._index = faiss.IndexFlatIP(self._dim)
            self._metadata = []
            return False

    def is_empty(self) -> bool:
        return self._index.ntotal == 0

    @property
    def total_vectors(self) -> int:
        return self._index.ntotal
