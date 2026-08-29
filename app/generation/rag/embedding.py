"""Fase 5 - Embeddings del corpus con multilingual-e5-small.

Modelo: intfloat/multilingual-e5-small (118M params, 384 dimensiones).

Por que este y no otro:
- El sistema pregunta y responde en espanol (el CAG de Fase 4 ya lo hace). El
  embedding por defecto de Chroma es all-MiniLM-L6-v2, entrenado solo en
  ingles: degradaria el retrieval justo en el idioma del sistema.
- Cero dependencias nuevas. transformers y torch ya estan instalados y
  funcionando en GPU desde la Fase 3. sentence-transformers seria una
  envoltura comoda, pero no aporta nada que no resuelvan ~15 lineas de mean
  pooling; con el calendario de este TFM, menos piezas moviendose gana.

SALVAGUARDA IMPORTANTE - los modelos E5 son ASIMETRICOS. Esperan el prefijo
"query: " en las consultas y "passage: " en los documentos indexados. Si se
mezclan, el retrieval NO da error: simplemente empeora en silencio, que es el
peor modo de fallo posible en un RAG. Por eso este modulo expone DOS funciones
separadas (embed_passages / embed_queries) y NUNCA una generica, y por eso el
store no usa la embedding_function por defecto de Chroma: la coleccion se crea
sin embedder propio y los vectores se pasan siempre explicitamente desde aqui.
"""
from __future__ import annotations

from functools import lru_cache

import torch
from transformers import AutoModel, AutoTokenizer

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384
MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 64

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


@lru_cache(maxsize=1)
def _load_model():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME).to(device).eval()
    return tokenizer, model, device


def _mean_pool(last_hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    """Media de los tokens reales (los de padding no cuentan). Es el pooling que
    usa E5; usar el token [CLS] en su lugar degradaria la calidad."""
    mask = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
    return (last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-9)


def _embed(texts: list[str], prefix: str, batch_size: int, verbose: bool) -> list[list[float]]:
    tokenizer, model, device = _load_model()
    vectors: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = [prefix + t for t in texts[start : start + batch_size]]
        encoded = tokenizer(
            batch, padding=True, truncation=True, max_length=MAX_LENGTH, return_tensors="pt"
        ).to(device)
        with torch.no_grad():
            output = model(**encoded)
        pooled = _mean_pool(output.last_hidden_state, encoded["attention_mask"])
        # normalizacion L2: con vectores unitarios la distancia coseno de Chroma
        # es equivalente al producto escalar, y las puntuaciones son comparables
        # entre consultas.
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        vectors.extend(pooled.cpu().tolist())

        if verbose and start and start % (batch_size * 50) == 0:
            print(f"[embed] {start}/{len(texts)}", flush=True)

    return vectors


def embed_passages(
    texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE, verbose: bool = False
) -> list[list[float]]:
    """Embeddings para INDEXAR. Prefijo 'passage: '. No usar para consultas."""
    return _embed(texts, PASSAGE_PREFIX, batch_size, verbose)


def embed_queries(
    texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE, verbose: bool = False
) -> list[list[float]]:
    """Embeddings para CONSULTAR. Prefijo 'query: '. No usar para indexar."""
    return _embed(texts, QUERY_PREFIX, batch_size, verbose)
