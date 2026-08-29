"""Fase 5 - Trocea los documentos del corpus en chunks indexables.

Decision de diseno: la estrategia depende del TIPO de fuente, no es unica.

- Fichas estructuradas (compuesto, cribado, binding, patogeno, metodologia):
  el chunk es el registro completo, SIN ventana deslizante ni solape. Miden
  entre 200 y 1900 caracteres (mediana ~850). Una ventana deslizante sobre
  registros estructurados parte un compuesto por la mitad y pega el final de
  uno con el principio del siguiente: produciria exactamente el chunk que hace
  atribuir un MIC al compuesto equivocado, que es el fallo que esta fase existe
  para evitar.
- Abstracts de literatura: un abstract = un chunk. Solo se parte si supera
  MAX_CHARS, y entonces por parrafo/frase, nunca a mitad de numero. Cada trozo
  hereda la metadata (y por tanto el PMID) del articulo entero, para que la
  cita siga siendo correcta en cualquier fragmento.
"""
from __future__ import annotations

import re

from app.generation.rag.corpus import EvidenceDoc

MAX_CHARS = 1500
OVERLAP_CHARS = 150
STRUCTURED_CLASSES = {
    "phenotypic_potency",
    "primary_screen_summary",
    "binding_specific",
    "background",
    "methodology",
}


def _split_long_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Corta por limite de frase/parrafo dentro de la ventana, para no partir
    un valor numerico ni una cita por la mitad."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        window = text[start:end]
        # preferencia: salto de parrafo > fin de frase > espacio
        cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
        if cut < max_chars // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        chunks.append(text[start : start + cut].strip())
        start = start + cut - overlap
    return [c for c in chunks if c]


def chunk_documents(documents: list[EvidenceDoc]) -> list[EvidenceDoc]:
    """Devuelve los chunks listos para indexar, con la metadata heredada."""
    chunks: list[EvidenceDoc] = []
    for doc in documents:
        evidence_class = doc.metadata.get("evidence_class", "")
        if evidence_class in STRUCTURED_CLASSES or len(doc.text) <= MAX_CHARS:
            chunks.append(
                EvidenceDoc(
                    doc_id=doc.doc_id,
                    text=doc.text,
                    metadata={**doc.metadata, "chunk_index": 0, "n_chunks": 1},
                )
            )
            continue

        parts = _split_long_text(doc.text, MAX_CHARS, OVERLAP_CHARS)
        header = doc.text.split("\n\nAbstract:")[0]
        for i, part in enumerate(parts):
            # el encabezado (titulo, revista, PMID) se repite en cada trozo:
            # un fragmento recuperado suelto tiene que seguir siendo citable.
            body = part if i == 0 else f"{header}\n\n(fragmento {i + 1}/{len(parts)})\n\n{part}"
            chunks.append(
                EvidenceDoc(
                    doc_id=f"{doc.doc_id}#{i}",
                    text=body,
                    metadata={**doc.metadata, "chunk_index": i, "n_chunks": len(parts)},
                )
            )
    return chunks


def chunk_stats(chunks: list[EvidenceDoc]) -> dict:
    lengths = sorted(len(c.text) for c in chunks)
    if not lengths:
        return {}
    return {
        "n_chunks": len(lengths),
        "chars_min": lengths[0],
        "chars_median": lengths[len(lengths) // 2],
        "chars_p95": lengths[int(0.95 * (len(lengths) - 1))],
        "chars_max": lengths[-1],
    }
