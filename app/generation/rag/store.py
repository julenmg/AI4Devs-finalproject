"""Fase 5 - Indice vectorial local (Chroma) con persistencia en disco.

Chroma embebido y no Postgres+pgvector: el dataset ChEMBL/CO-ADD es de solo
lectura una vez curado, sin relaciones ni transacciones, y no hay datos
transaccionales propios que justifiquen levantar y mantener un servidor de base
de datos (decision registrada en docs/decisions.md, Fase 5).

La coleccion se crea SIN embedding_function: los vectores se calculan siempre
en embedding.py y se pasan explicitamente. Es deliberado - si Chroma pudiera
embeber por su cuenta, usaria su modelo por defecto (all-MiniLM-L6-v2, ingles)
y ademas aplicaria el mismo tratamiento a documentos y consultas, rompiendo la
asimetria query:/passage: que exige E5.
"""
from __future__ import annotations

from pathlib import Path

import chromadb

from app.generation.rag.chunking import EvidenceDoc
from app.generation.rag.embedding import embed_passages, embed_queries

DEFAULT_PERSIST_DIR = Path("data/chroma_db")
COLLECTION_NAME = "eskapeguard_evidence"
# Chroma limita el tamano de cada lote de insercion; se trocea por debajo del
# maximo para no depender del valor exacto de la version instalada.
UPSERT_BATCH = 2000


def get_vector_store(
    persist_dir: Path | str = DEFAULT_PERSIST_DIR, reset: bool = False
) -> chromadb.api.models.Collection.Collection:
    persist_dir = Path(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir))

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # no existia todavia

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )


def _clean_metadata(metadata: dict) -> dict:
    """Chroma solo admite escalares (str/int/float/bool) y rechaza None. Los
    campos vacios se descartan en vez de guardarse como cadena vacia, para que
    un filtro por ese campo no encuentre falsos positivos."""
    clean = {}
    for key, value in metadata.items():
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def index_documents(
    chunks: list[EvidenceDoc],
    persist_dir: Path | str = DEFAULT_PERSIST_DIR,
    reset: bool = True,
    batch_size: int = 64,
    verbose: bool = True,
) -> int:
    """Embebe e inserta INTERCALANDO por lotes: cada ventana se embebe y se
    escribe antes de pasar a la siguiente.

    No es un detalle de estilo. Embeber los 34k chunks enteros antes de escribir
    nada significa que un proceso interrumpido a los 14 minutos pierde los 14
    minutos (paso exactamente eso en el primer intento). Intercalando, lo ya
    escrito persiste y `--no-reset` permite retomar.
    """
    collection = get_vector_store(persist_dir, reset=reset)
    if verbose:
        print(f"[store] embebiendo e indexando {len(chunks)} chunks con "
              f"multilingual-e5-small...", flush=True)

    for start in range(0, len(chunks), UPSERT_BATCH):
        window = chunks[start : start + UPSERT_BATCH]
        # se embebe search_text (compacto y distintivo), se guarda text (citable)
        vectors = embed_passages([c.embed_text() for c in window], batch_size=batch_size)
        collection.upsert(
            ids=[c.doc_id for c in window],
            documents=[c.text for c in window],
            metadatas=[_clean_metadata(c.metadata) for c in window],
            embeddings=vectors,
        )
        if verbose:
            done = min(start + UPSERT_BATCH, len(chunks))
            print(f"[store] {done}/{len(chunks)} ({done / len(chunks):.0%})", flush=True)

    return collection.count()


def search(
    query: str,
    k: int = 8,
    where: dict | None = None,
    persist_dir: Path | str = DEFAULT_PERSIST_DIR,
) -> list[dict]:
    """Busqueda semantica con prefiltro opcional por metadata.

    Devuelve una lista de dicts con text/metadata/distance, ordenada de mas a
    menos relevante."""
    collection = get_vector_store(persist_dir)
    vector = embed_queries([query])[0]
    result = collection.query(
        query_embeddings=[vector],
        n_results=k,
        where=where or None,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for i in range(len(result["ids"][0])):
        hits.append(
            {
                "doc_id": result["ids"][0][i],
                "text": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": result["distances"][0][i],
            }
        )
    return hits


def get_by_ids(ids: list[str], persist_dir: Path | str = DEFAULT_PERSIST_DIR) -> list[dict]:
    """Recuperacion exacta por id, sin busqueda vectorial. La usa el atajo lexico
    de retrieval.py: si la pregunta nombra un compuesto conocido, su ficha entra
    con certeza en la evidencia en vez de depender de que el top-k la alcance."""
    if not ids:
        return []
    collection = get_vector_store(persist_dir)
    result = collection.get(ids=ids, include=["documents", "metadatas"])
    return [
        {
            "doc_id": result["ids"][i],
            "text": result["documents"][i],
            "metadata": result["metadatas"][i],
            "distance": 0.0,  # coincidencia exacta de nombre, no distancia vectorial
        }
        for i in range(len(result["ids"]))
    ]
