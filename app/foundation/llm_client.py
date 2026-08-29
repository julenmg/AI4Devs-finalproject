"""Cliente LLM compartido por CAG, RAG y el agente (Fases 4-6).

Un solo punto de lectura de la API key de Anthropic para que los modulos de
generation/ no repitan la logica de configuracion. Cacheado con lru_cache:
el SDK es thread-safe y basta una instancia por proceso.
"""
from functools import lru_cache

import anthropic

from app.config import settings


@lru_cache(maxsize=1)
def get_llm_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY vacia. Copia .env.example a .env y "
            "rellena la clave antes de usar CAG/RAG/agente."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
