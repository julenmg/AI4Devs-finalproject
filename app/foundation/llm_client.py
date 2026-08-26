"""Cliente LLM compartido por CAG, RAG y el agente (Fases 4-6).

TODO: envolver el cliente de Anthropic en una funcion comun, para no repetir
la config de API key en cada modulo de generation/.
"""


def get_llm_client():
    raise NotImplementedError
