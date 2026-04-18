"""
retriever.py — Búsqueda top-k + re-ranking ligero sin modelo externo.

Re-ranking:
  score_final = 0.7 × similitud_coseno + 0.3 × solapamiento_keywords

El coeficiente 0.7/0.3 prioriza la similitud semántica (embeddings)
pero añade un boost léxico para términos técnicos que los embeddings
pueden subestimar (acrónimos, nombres propios, código).
"""
from __future__ import annotations

from rag.config import TOP_K
from rag.embeddings import EmbeddingProvider
from rag.vectorstore import FAISSVectorStore


class Retriever:
    """
    Recupera los k chunks más relevantes para una query.

    Flujo:
      1. Vectorizar la query con el mismo proveedor que en ingesta.
      2. Recuperar top candidates_k de FAISS (candidates_k = top_k × 3).
      3. Re-rankear con score combinado semántico + léxico.
      4. Devolver los top_k mejores tras re-ranking.
    """

    def __init__(
        self,
        vector_store: FAISSVectorStore,
        embedding_provider: EmbeddingProvider,
        top_k: int = TOP_K,
        rerank_factor: int = 3,
    ) -> None:
        self._store = vector_store
        self._embedder = embedding_provider
        self._top_k = top_k
        # Recuperar rerank_factor × top_k candidatos para el re-ranking
        self._rerank_factor = rerank_factor

    def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        """
        Recupera los chunks más relevantes para la query.

        Args:
            query:  Pregunta del usuario en texto libre.
            top_k:  Override del top_k configurado. None usa el default.

        Returns:
            Lista de chunks ordenados por relevancia (mejor primero),
            cada uno con campos: text, source, chunk_id, score, rerank_score.
        """
        k = top_k if top_k is not None else self._top_k
        candidates_k = k * self._rerank_factor

        query_vector = self._embedder.embed_query(query)
        candidates = self._store.search(query_vector, top_k=candidates_k)

        if not candidates:
            return []

        reranked = self._rerank(query, candidates)
        return reranked[:k]

    # ── Re-ranking ────────────────────────────────────────────────────────────

    def _rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """
        Re-rankea candidatos con score combinado:
          0.7 × cosine_sim + 0.3 × keyword_overlap

        No requiere modelo externo. Ejecuta en microsegundos.
        """
        query_tokens = set(self._tokenize(query))

        scored: list[dict] = []
        for chunk in candidates:
            cosine = chunk.get("score", 0.0)
            keyword_score = self._keyword_overlap(query_tokens, chunk["text"])
            final_score = 0.7 * cosine + 0.3 * keyword_score

            item = dict(chunk)
            item["rerank_score"] = round(final_score, 6)
            scored.append(item)

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokeniza eliminando puntuación y palabras cortas (stopwords ligero)."""
        tokens = []
        for word in text.split():
            clean = word.lower().strip(".,;:!?\"'()[]{}-/\\")
            if len(clean) > 2:
                tokens.append(clean)
        return tokens

    @staticmethod
    def _keyword_overlap(query_tokens: set[str], chunk_text: str) -> float:
        """
        Fracción de tokens de la query que aparecen en el chunk.
        Rango: 0.0 (sin solapamiento) a 1.0 (todos los términos presentes).
        """
        if not query_tokens:
            return 0.0
        chunk_tokens = {
            w.lower().strip(".,;:!?\"'()[]{}-/\\")
            for w in chunk_text.split()
            if len(w) > 2
        }
        overlap = len(query_tokens & chunk_tokens)
        return overlap / len(query_tokens)
