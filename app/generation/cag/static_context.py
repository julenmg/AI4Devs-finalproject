"""Fase 4 - Prototipo CAG: LLM con contexto fijo (ficha del patogeno diana,
mecanismos de resistencia conocidos), SIN retrieval ni modelo entrenado.

Documenta en el README donde se rompe este enfoque (no escala a mas
patogenos, no puede citar evidencia real mas alla de lo que esta a mano) -
esa limitacion es la que justifica pasar a RAG en la Fase 5.
"""


def answer_with_static_context(question: str) -> str:
    raise NotImplementedError
