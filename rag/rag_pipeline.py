"""
rag_pipeline.py — Orquestador RAG: contexto comprimido + llamada real a Claude.

Arquitectura de memoria:
  - Memoria corto plazo (ShortTermMemory): conversación en curso,
    últimos N turnos en memoria RAM. Se resetea al iniciar nueva sesión.
  - Memoria largo plazo (LongTermMemory): base de conocimiento persistente
    en FAISS. Sobrevive entre ejecuciones.

Flujo por query:
  1. Recuperar chunks relevantes (LongTermMemory → Retriever → FAISS)
  2. Comprimir contexto al presupuesto de tokens (ContextCompressor)
  3. Construir prompt: contexto + historial corto + pregunta actual
  4. Llamar a Claude API con el prompt completo
  5. Actualizar memoria corta con respuesta
  6. Devolver respuesta al usuario
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
import tiktoken

from rag.config import (
    API_KEY_ANTHROPIC,
    CLAUDE_MODEL,
    MAX_CONTEXT_TOKENS,
    SHORT_TERM_MEMORY_TURNS,
    TOP_K,
)
from rag.embeddings import OpenAIEmbeddings
from rag.retriever import Retriever
from rag.vectorstore import FAISSVectorStore

logger = logging.getLogger(__name__)


# ── Memoria corto plazo ───────────────────────────────────────────────────────

@dataclass
class Message:
    role: str      # "user" | "assistant"
    content: str


@dataclass
class ShortTermMemory:
    """
    Historial de conversación en RAM — últimos N turnos.

    Se pasa directamente a la API de Claude como historial de mensajes,
    permitiendo preguntas de seguimiento con contexto conversacional.
    """
    max_turns: int = SHORT_TERM_MEMORY_TURNS
    messages: list[Message] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))
        # Mantener solo los últimos max_turns pares (2 mensajes por turno)
        max_messages = self.max_turns * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def to_api_format(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def clear(self) -> None:
        """Reiniciar sesión de conversación."""
        self.messages.clear()

    def __len__(self) -> int:
        return len(self.messages)


# ── Memoria largo plazo ───────────────────────────────────────────────────────

class LongTermMemory:
    """
    Base de conocimiento persistente respaldada por FAISS.

    Sobrevive entre ejecuciones. Permite preguntas sobre cualquier
    documento ingestado en sesiones anteriores.
    """

    def __init__(self, vector_store: FAISSVectorStore, retriever: Retriever) -> None:
        self._store = vector_store
        self._retriever = retriever

    def ingest(self, chunks: list[dict[str, Any]]) -> None:
        """Añadir chunks a la base de conocimiento (incremental)."""
        self._store.add_documents(chunks)

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """Recuperar chunks relevantes para la query."""
        return self._retriever.retrieve(query, top_k=top_k)

    def is_empty(self) -> bool:
        return self._store.is_empty()

    @property
    def total_vectors(self) -> int:
        return self._store.total_vectors


# ── Compresión de contexto ────────────────────────────────────────────────────

class ContextCompressor:
    """
    Construye un string de contexto que cabe en el presupuesto de tokens.

    Estrategia:
      - Chunks ya ordenados por rerank_score (mejor primero).
      - Incluir chunks completos hasta agotar el presupuesto.
      - Si el último chunk no cabe completo, truncar en el último
        punto/salto de línea para no cortar una frase a mitad.
    """

    def __init__(
        self,
        max_tokens: int = MAX_CONTEXT_TOKENS,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self._max_tokens = max_tokens
        self._enc = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    def compress(self, chunks: list[dict], header: str = "") -> str:
        """
        Construye el contexto respetando el límite de tokens.

        Args:
            chunks: Lista de chunks ordenados por relevancia.
            header: Texto fijo que precede al contexto (se descuenta del budget).

        Returns:
            String de contexto listo para insertar en el prompt.
        """
        budget = self._max_tokens - self.count_tokens(header)
        parts: list[str] = []
        used = 0

        for chunk in chunks:
            snippet = f"[Fuente: {chunk.get('source', 'desconocida')}]\n{chunk['text']}\n"
            tokens = self.count_tokens(snippet)

            if used + tokens <= budget:
                parts.append(snippet)
                used += tokens
            else:
                remaining = budget - used
                if remaining > 80:
                    # Incluir versión truncada del último chunk
                    truncated = self._truncate_to_tokens(chunk["text"], remaining - 40)
                    if truncated:
                        parts.append(
                            f"[Fuente: {chunk.get('source', 'desconocida')}]\n{truncated}\n"
                        )
                break

        return "\n---\n".join(parts)

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncar texto al límite de tokens, terminando en frase completa si es posible."""
        tokens = self._enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = self._enc.decode(tokens[:max_tokens])
        # Intentar terminar en frase completa
        for sep in (".\n", ".\n\n", ". ", "!", "?", "\n"):
            idx = truncated.rfind(sep)
            if idx > len(truncated) // 2:
                return truncated[: idx + len(sep)].rstrip()
        return truncated.rstrip()


# ── Pipeline principal ────────────────────────────────────────────────────────

class RAGPipeline:
    """
    Orquestador del sistema RAG completo.

    Combina:
      - Memoria corto plazo (conversación)
      - Memoria largo plazo (FAISS + retriever)
      - Compresión de contexto
      - Llamada real a Claude API (Anthropic)

    Uso básico:
        pipeline = RAGPipeline()
        pipeline.ingest(chunks)         # una vez
        respuesta = pipeline.query("¿Qué es X?")
        print(respuesta)
    """

    SYSTEM_PROMPT = (
        "Eres un asistente experto. Responde ÚNICAMENTE usando el contexto proporcionado. "
        "Si el contexto no contiene información suficiente para responder, indícalo claramente. "
        "Sé preciso, conciso y cita la fuente cuando sea relevante."
    )

    def __init__(
        self,
        vector_store: FAISSVectorStore | None = None,
        embedding_provider: OpenAIEmbeddings | None = None,
        top_k: int = TOP_K,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
    ) -> None:
        embedder = embedding_provider or OpenAIEmbeddings()
        store = vector_store or FAISSVectorStore(embedder)
        retriever = Retriever(store, embedder, top_k=top_k)

        self._long_term = LongTermMemory(store, retriever)
        self._short_term = ShortTermMemory()
        self._compressor = ContextCompressor(max_tokens=max_context_tokens)
        self._client = anthropic.Anthropic(api_key=API_KEY_ANTHROPIC)
        self._model = CLAUDE_MODEL
        self._top_k = top_k

    # ── API pública ───────────────────────────────────────────────────────────

    def ingest(self, chunks: list[dict[str, Any]]) -> None:
        """
        Añadir chunks a la memoria largo plazo (FAISS).
        Operación incremental: no reconstruye el índice.
        """
        before = self._long_term.total_vectors
        self._long_term.ingest(chunks)
        after = self._long_term.total_vectors
        logger.info(
            "Ingesta completada: %d chunks nuevos (total vectores: %d)",
            after - before, after,
        )

    def query(self, user_message: str) -> str:
        """
        Pipeline RAG completo para una pregunta del usuario.

        1. Recuperar chunks relevantes de memoria largo plazo.
        2. Comprimir contexto al presupuesto de tokens.
        3. Construir prompt con contexto + historial de conversación.
        4. Llamar a Claude API.
        5. Actualizar memoria corto plazo.
        6. Devolver respuesta.

        Args:
            user_message: Pregunta del usuario en lenguaje natural.

        Returns:
            Respuesta generada por Claude fundamentada en el contexto recuperado.
        """
        # 1. Recuperar contexto relevante
        chunks = self._long_term.retrieve(user_message, top_k=self._top_k)

        # 2. Comprimir contexto
        context_header = "CONTEXTO RECUPERADO:\n"
        context_body = self._compressor.compress(chunks, header=context_header)

        # 3. Construir mensaje augmentado
        if context_body:
            augmented = f"{context_header}{context_body}\n\nPREGUNTA: {user_message}"
        else:
            logger.warning(
                "No se encontraron chunks relevantes para la query. "
                "Respondiendo sin contexto RAG."
            )
            augmented = user_message

        # 4. Actualizar memoria corta y construir historial
        self._short_term.add("user", augmented)
        messages = self._short_term.to_api_format()

        # 5. Llamada a Claude API
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self.SYSTEM_PROMPT,
            messages=messages,
        )
        answer: str = response.content[0].text

        # 6. Actualizar memoria corta con respuesta del asistente
        self._short_term.add("assistant", answer)

        logger.info(
            "Query respondida | chunks_recuperados=%d | tokens_entrada=%d | tokens_salida=%d",
            len(chunks),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return answer

    def clear_conversation(self) -> None:
        """Iniciar nueva sesión de conversación (vacía memoria corto plazo)."""
        self._short_term.clear()
        logger.info("Memoria corto plazo limpiada. Nueva sesión iniciada.")

    @property
    def knowledge_base_size(self) -> int:
        """Número de vectores en la base de conocimiento."""
        return self._long_term.total_vectors
