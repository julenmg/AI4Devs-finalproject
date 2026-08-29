"""Fase 5 - Construye el indice vectorial de EskapeGuard.

    uv run python -m scripts.build_index                      # solo ChEMBL + CO-ADD
    uv run python -m scripts.build_index --with-literature    # + abstracts de PubMed
    uv run python -m scripts.build_index --inspect "..."      # consulta de prueba

(se invoca con -m, como scripts/smoke_test.py: asi `app` esta en el path)

El corpus principal no toca la red: la literatura es un anadido opcional y, si
PubMed falla, el indice se construye igual con el resto de la evidencia.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from app.generation.rag.chunking import chunk_documents, chunk_stats
from app.generation.rag.corpus import build_corpus, compound_name_index
from app.generation.rag.retrieval import NAME_INDEX_PATH
from app.generation.rag.store import DEFAULT_PERSIST_DIR, index_documents, search


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye el indice RAG de EskapeGuard")
    parser.add_argument("--with-literature", action="store_true",
                        help="anade abstracts de PubMed al corpus")
    parser.add_argument("--refresh-literature", action="store_true",
                        help="fuerza la descarga aunque exista cache en data/raw/pubmed_*.json")
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIR))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--no-reset", action="store_true",
                        help="conserva la coleccion existente en vez de recrearla")
    parser.add_argument("--dry-run", action="store_true",
                        help="construye el corpus y muestra estadisticas sin indexar")
    parser.add_argument("--inspect", metavar="CONSULTA",
                        help="no construye nada: lanza una consulta contra el indice actual")
    args = parser.parse_args()

    if args.inspect:
        for hit in search(args.inspect, k=5, persist_dir=args.persist_dir):
            meta = hit["metadata"]
            print(f"\n[{hit['distance']:.4f}] {meta.get('evidence_class')} | {meta.get('citation')}")
            print(hit["text"][:300].replace("\n", " ") + "...")
        return

    started = time.time()
    docs = build_corpus()

    if args.with_literature:
        try:
            from app.generation.rag.literature import literature_docs

            docs += literature_docs(refresh=args.refresh_literature)
        except Exception as exc:  # noqa: BLE001 - la literatura es opcional por diseno
            print(f"[pubmed] FALLO ({type(exc).__name__}: {exc}). Se indexa sin literatura.")

    chunks = chunk_documents(docs)
    print(f"[chunking] {len(docs)} documentos -> {len(chunks)} chunks")
    print(f"[chunking] {json.dumps(chunk_stats(chunks))}")
    print(f"[chunking] por clase: {dict(Counter(c.metadata['evidence_class'] for c in chunks))}")

    names = compound_name_index(chunks)
    print(f"[nombres] {len(names)} compuestos nombrados para busqueda lexica")

    if args.dry_run:
        print(f"[dry-run] nada indexado. {time.time() - started:.1f}s")
        return

    NAME_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    NAME_INDEX_PATH.write_text(json.dumps(names, ensure_ascii=False))

    total = index_documents(
        chunks,
        persist_dir=args.persist_dir,
        reset=not args.no_reset,
        batch_size=args.batch_size,
    )
    print(f"\n[ok] {total} chunks en la coleccion ({args.persist_dir}) "
          f"en {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
