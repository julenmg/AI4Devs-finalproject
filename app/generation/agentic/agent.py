"""Fase 6 - Orquesta el agente: recibe una consulta de reposicionamiento
("que farmacos ya aprobados podrian funcionar contra este patogeno?"),
decide que herramientas usar (RAG y/o modelo DTI) y compone la respuesta
final con evidencia y prediccion de afinidad.

TODO: implementar el bucle del agente (puede ser tool-calling simple con el
SDK de Anthropic).
"""


def run_agent(query: str) -> str:
    raise NotImplementedError
