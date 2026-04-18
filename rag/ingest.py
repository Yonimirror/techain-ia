"""
ingest.py — Carga de documentos (.txt y .pdf) y chunking configurable.

Chunking por palabras (no caracteres): preserva palabras completas
y aplica solapamiento configurable para no perder contexto en los bordes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from rag.config import CHUNK_OVERLAP, CHUNK_SIZE, RUTA_DOCUMENTOS


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text and text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


def load_document(path: Path) -> str:
    """Carga un archivo .txt o .pdf y devuelve el texto plano."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_txt(path)
    elif suffix == ".pdf":
        return load_pdf(path)
    else:
        raise ValueError(f"Tipo de archivo no soportado: {suffix!r}. Solo .txt y .pdf.")


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """
    Divide el texto en chunks de ~chunk_size caracteres con solapamiento.

    - Splits por palabras para no cortar a mitad de una palabra.
    - Solapamiento medido en caracteres para consistencia con chunk_size.
    - Garantiza que cada chunk tenga al menos una palabra.

    Args:
        text:       Texto completo a dividir.
        chunk_size: Tamaño objetivo de cada chunk en caracteres.
        overlap:    Caracteres de solapamiento entre chunks consecutivos.

    Returns:
        Lista de strings, cada uno es un chunk de texto.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start_word = 0

    while start_word < len(words):
        # Acumular palabras hasta alcanzar chunk_size
        end_word = start_word
        current_len = 0
        while end_word < len(words):
            word_len = len(words[end_word]) + (1 if end_word > start_word else 0)
            if current_len + word_len > chunk_size and end_word > start_word:
                break
            current_len += word_len
            end_word += 1

        chunk = " ".join(words[start_word:end_word])
        chunks.append(chunk)

        if end_word >= len(words):
            break

        # Calcular el retroceso de solapamiento en palabras
        overlap_len = 0
        back = end_word - 1
        while back > start_word and overlap_len < overlap:
            overlap_len += len(words[back]) + 1
            back -= 1
        next_start = back + 1

        # Avanzar siempre al menos una palabra para evitar bucle infinito
        start_word = max(next_start, start_word + 1)

    return chunks


# ── Ingesta de directorio ─────────────────────────────────────────────────────

def ingest_directory(
    directory: str = RUTA_DOCUMENTOS,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    """
    Carga todos los archivos .txt y .pdf del directorio y devuelve sus chunks.

    Cada chunk es un dict:
        {
          "text":         str,   # contenido del chunk
          "source":       str,   # nombre del archivo origen
          "chunk_id":     int,   # índice del chunk dentro del documento
          "total_chunks": int,   # total de chunks del documento
          "doc_hash":     str,   # MD5 del documento completo (para deduplicación)
        }

    Args:
        directory:  Ruta al directorio con los documentos.
        chunk_size: Tamaño objetivo de chunk en caracteres.
        overlap:    Solapamiento en caracteres entre chunks.

    Returns:
        Lista de chunks con metadatos. Ordenados por archivo y chunk_id.
    """
    doc_path = Path(directory)
    if not doc_path.exists():
        raise FileNotFoundError(
            f"Directorio de documentos no encontrado: {directory!r}\n"
            f"Crea la carpeta y añade archivos .txt o .pdf."
        )

    all_chunks: list[dict[str, Any]] = []

    supported = {".txt", ".pdf"}
    files = sorted(p for p in doc_path.iterdir() if p.suffix.lower() in supported)

    if not files:
        raise ValueError(f"No se encontraron archivos .txt ni .pdf en: {directory!r}")

    for file_path in files:
        raw_text = load_document(file_path)
        if not raw_text.strip():
            continue

        doc_hash = hashlib.md5(raw_text.encode("utf-8")).hexdigest()
        text_chunks = chunk_text(raw_text, chunk_size, overlap)

        for idx, chunk in enumerate(text_chunks):
            all_chunks.append({
                "text": chunk,
                "source": file_path.name,
                "chunk_id": idx,
                "total_chunks": len(text_chunks),
                "doc_hash": doc_hash,
            })

    return all_chunks
