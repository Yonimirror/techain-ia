"""
demo.py — Ejemplo end-to-end ejecutable del sistema RAG.

Demuestra:
  1. Ingesta de un documento .txt
  2. Consulta real al sistema
  3. Respuesta generada por Claude con contexto recuperado
  4. Segunda pregunta (memoria corto plazo activa)

Ejecución:
    python -m rag.demo
    # o desde el directorio raíz del proyecto:
    python rag/demo.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Asegurar que el directorio raíz del proyecto está en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.embeddings import OpenAIEmbeddings
from rag.ingest import ingest_directory
from rag.rag_pipeline import RAGPipeline
from rag.vectorstore import FAISSVectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rag.demo")

DOCS_DIR = str(Path(__file__).parent / "docs")


def separator(title: str = "") -> None:
    line = "─" * 60
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(f"{line}")
    else:
        print(f"\n{line}\n")


def main() -> None:
    separator("SISTEMA RAG — DEMO END-TO-END")

    # ── 1. Inicializar pipeline ────────────────────────────────────────────
    print("\n[1/4] Inicializando pipeline RAG...")
    embedder = OpenAIEmbeddings()
    store = FAISSVectorStore(embedder)
    pipeline = RAGPipeline(vector_store=store, embedding_provider=embedder, top_k=3)

    print(f"      Vectores en índice (pre-ingesta): {pipeline.knowledge_base_size}")

    # ── 2. Ingesta de documentos ──────────────────────────────────────────
    separator("FASE 1: INGESTA")
    print(f"Directorio: {DOCS_DIR}")

    chunks = ingest_directory(directory=DOCS_DIR)
    print(f"Chunks generados: {len(chunks)}")
    for chunk in chunks[:2]:
        preview = chunk["text"][:80].replace("\n", " ")
        print(f"  [{chunk['source']} chunk#{chunk['chunk_id']}] {preview}...")

    pipeline.ingest(chunks)
    print(f"\nVectores en índice (post-ingesta): {pipeline.knowledge_base_size}")

    # ── 3. Primera consulta ───────────────────────────────────────────────
    separator("FASE 2: CONSULTA 1")
    pregunta_1 = "¿Cuáles son las ventajas del enfoque RAG frente al fine-tuning?"
    print(f"Pregunta: {pregunta_1}\n")

    respuesta_1 = pipeline.query(pregunta_1)
    print(f"Respuesta de Claude:\n\n{respuesta_1}")

    # ── 4. Segunda consulta (memoria corta activa) ─────────────────────
    separator("FASE 3: CONSULTA 2 (memoria corto plazo activa)")
    pregunta_2 = "¿Y cuándo debería usar FAISS vs Pinecone?"
    print(f"Pregunta de seguimiento: {pregunta_2}\n")
    print("(Claude recuerda el contexto de la pregunta anterior)\n")

    respuesta_2 = pipeline.query(pregunta_2)
    print(f"Respuesta de Claude:\n\n{respuesta_2}")

    # ── 5. Reset de conversación ──────────────────────────────────────────
    separator("FASE 4: RESET DE CONVERSACIÓN")
    pipeline.clear_conversation()
    print("Memoria corto plazo limpiada. Nueva sesión iniciada.")
    print(f"Base de conocimiento intacta: {pipeline.knowledge_base_size} vectores\n")

    separator("DEMO COMPLETADA")
    print("El índice FAISS está guardado en rag/data/ y persistirá entre ejecuciones.")
    print("La próxima ejecución NO re-ingestará documentos ya indexados (deduplicación).\n")


if __name__ == "__main__":
    main()
