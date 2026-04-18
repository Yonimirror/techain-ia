"""
config.py — Variables globales, constantes y rutas del sistema RAG.

Todas las variables se leen desde variables de entorno (o .env).
No hay valores hardcodeados fuera de este archivo.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Rutas ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
FAISS_INDEX_PATH = DATA_DIR / "faiss.index"
FAISS_METADATA_PATH = DATA_DIR / "faiss_metadata.json"

# ── API Keys ──────────────────────────────────────────────────────────────────
API_KEY_OPENAI: str = os.environ["API_KEY_OPENAI"]
API_KEY_ANTHROPIC: str = os.environ["API_KEY_ANTHROPIC"]

# ── Documentos ────────────────────────────────────────────────────────────────
RUTA_DOCUMENTOS: str = os.getenv("RUTA_DOCUMENTOS", str(BASE_DIR / "docs"))

# ── Chunking ──────────────────────────────────────────────────────────────────
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "64"))

# ── Recuperación ─────────────────────────────────────────────────────────────
TOP_K: int = int(os.getenv("TOP_K", "5"))

# ── LLM ──────────────────────────────────────────────────────────────────────
CLAUDE_MODEL: str = "claude-sonnet-4-6"
MAX_CONTEXT_TOKENS: int = 4096       # tokens reservados para el contexto RAG
SHORT_TERM_MEMORY_TURNS: int = 10    # pares usuario/asistente en memoria corta

# ── Embeddings ────────────────────────────────────────────────────────────────
EMBEDDING_MODEL: str = "text-embedding-3-small"
EMBEDDING_DIM: int = 1536
