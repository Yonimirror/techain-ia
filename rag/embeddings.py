"""
embeddings.py — Interfaz base + implementación OpenAI (patrón adaptador).

Para cambiar de proveedor: crear nueva subclase de EmbeddingProvider
e inyectarla en FAISSVectorStore y Retriever. Sin tocar nada más.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from openai import OpenAI

from rag.config import API_KEY_OPENAI, EMBEDDING_DIM, EMBEDDING_MODEL


class EmbeddingProvider(ABC):
    """
    Interfaz abstracta para proveedores de embeddings.
    Cualquier implementación (OpenAI, Cohere, HuggingFace, local) debe subclasear esto.
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensión del vector de embedding."""
        ...

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed un batch de textos.
        Returns: lista de vectores float, uno por texto.
        """
        ...

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Embed una única query. Los vectores deben estar normalizados (L2)."""
        ...


class OpenAIEmbeddings(EmbeddingProvider):
    """
    Adaptador para OpenAI text-embedding-3-small.

    Normaliza todos los vectores a L2=1 para que el producto interno
    sea equivalente a similitud coseno en el índice FAISS IndexFlatIP.
    """

    def __init__(
        self,
        api_key: str = API_KEY_OPENAI,
        model: str = EMBEDDING_MODEL,
        batch_size: int = 512,
    ) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._batch_size = batch_size

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed en batches y normaliza cada vector."""
        if not texts:
            return []
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            batch_vectors = [item.embedding for item in response.data]
            all_vectors.extend(batch_vectors)
        return [self._normalize(v) for v in all_vectors]

    def embed_query(self, query: str) -> list[float]:
        """Embed una query individual normalizada."""
        response = self._client.embeddings.create(model=self._model, input=[query])
        return self._normalize(response.data[0].embedding)

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        arr = np.array(vector, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr.tolist()
