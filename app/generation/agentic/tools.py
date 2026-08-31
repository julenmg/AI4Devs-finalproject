"""Fase 6 - Herramientas que el agente puede invocar.

Dos, y solo dos: recuperar evidencia (RAG de Fase 5) y predecir potencia
(modelo DTI + LoRA de Fase 3). Cada una devuelve un dict con su procedencia
marcada, para que en el contexto del agente nunca se confunda un dato medido con
uno predicho.

INDEPENDENCIA DEL DTI (advertencia de docs/decisions.md, Fase 5). El valor que
devuelve `predict_affinity` se calcula ANTES de que el LLM vea evidencia alguna
y viaja como un numero ya sellado en el resultado de la herramienta. El texto que
el modelo genere no puede cambiarlo: lo que se muestra al usuario sale de aqui,
no de la respuesta. `agent.py` ademas comprueba a posteriori que el valor citado
en la respuesta coincida con el que devolvio la herramienta.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from app.config import settings
from app.generation.rag.corpus import _slug
from app.generation.rag.retrieval import answer_with_retrieval, retrieve

# Se importa perezosamente: cargar el modelo cuesta ~53 s y la mayoria de las
# consultas del agente no lo necesitan.
_MODEL_CACHE: dict = {}


TOOL_SCHEMAS = [
    {
        "name": "retrieve_evidence",
        "description": (
            "Recupera evidencia experimental real del indice de EskapeGuard "
            "(ChEMBL, CO-ADD y abstracts de PubMed) para una pregunta. Devuelve "
            "fragmentos con su cita construida por codigo. Usala SIEMPRE que "
            "necesites un dato experimental, un mecanismo de resistencia o una "
            "referencia bibliografica. No inventes nada que no venga de aqui."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pregunta": {
                    "type": "string",
                    "description": "Consulta en lenguaje natural, en espanol.",
                },
                "patogeno": {
                    "type": "string",
                    "enum": settings.pathogens,
                    "description": "Filtra la evidencia a este patogeno. Omitir para comparar.",
                },
            },
            "required": ["pregunta"],
        },
    },
    {
        "name": "predict_affinity",
        "description": (
            "Predice la potencia fenotipica (pMIC) de un compuesto frente a un "
            "patogeno con el modelo DTI ajustado con LoRA. Es una PREDICCION del "
            "modelo, no una medida experimental, y su error tipico es de ~1 "
            "unidad de pMIC (un orden de magnitud en potencia). Acepta el nombre "
            "del compuesto (si esta en el cribado precomputado) o su SMILES. "
            "No la uses para afirmar eficacia clinica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "compuesto": {
                    "type": "string",
                    "description": "Nombre del compuesto, o su SMILES.",
                },
                "patogeno": {"type": "string", "enum": settings.pathogens},
            },
            "required": ["compuesto", "patogeno"],
        },
    },
    {
        "name": "consultar_cribado",
        "description": (
            "Consulta el cribado de reposicionamiento ya calculado sobre la "
            "coleccion clinica NIH y sobre los compuestos activos frente al otro "
            "patogeno. Devuelve candidatos ordenados por pMIC predicho, con su "
            "cubo, su evidencia experimental real y las marcas de contaminacion "
            "del entrenamiento. Usala para preguntas de tipo 'que candidatos hay'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patogeno": {"type": "string", "enum": settings.pathogens},
                "cubo": {
                    "type": "string",
                    "description": (
                        "Filtra por cubo: recuperacion, desacuerdo_modelo_experimento, "
                        "concordancia_negativa, hipotesis_transferencia."
                    ),
                },
                "top": {"type": "integer", "description": "Cuantos devolver (por defecto 10)."},
            },
            "required": ["patogeno"],
        },
    },
]


@lru_cache(maxsize=2)
def load_screen(pathogen: str) -> pd.DataFrame:
    path = settings.data_processed_dir / f"repurposing_screen_{_slug(pathogen)}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta primero: "
            "uv run python -m scripts.screen_repurposing"
        )
    return pd.read_csv(path)


def _get_model():
    if "model" not in _MODEL_CACHE:
        from scripts.screen_repurposing import load_finetuned
        from training.lora_finetune import _fetch_gyra_sequence

        model, tokenizer = load_finetuned()
        _MODEL_CACHE["model"] = model
        _MODEL_CACHE["tokenizer"] = tokenizer
        _MODEL_CACHE["anchors"] = {p: _fetch_gyra_sequence(p) for p in settings.pathogens}
    return _MODEL_CACHE["model"], _MODEL_CACHE["tokenizer"], _MODEL_CACHE["anchors"]


# --------------------------------------------------------------------------- #
# Implementaciones


def tool_retrieve_evidence(pregunta: str, patogeno: str | None = None) -> dict:
    where = {"pathogen": patogeno} if patogeno else None
    result = answer_with_retrieval(pregunta, where=where)
    return {
        "procedencia": "evidencia experimental recuperada del indice",
        "respuesta_rag": result["answer"],
        "evidencia": result["evidence"],
        "verificacion": result["verification"],
    }


def tool_predict_affinity(compuesto: str, patogeno: str) -> dict:
    """Resuelve nombre -> SMILES contra el cribado y predice.

    El valor devuelto es el que se mostrara al usuario. El LLM no lo recalcula
    ni lo ajusta: solo lo cita.
    """
    smiles, nombre, fuente = compuesto, compuesto, "SMILES proporcionado"
    reused = None

    try:
        screen = load_screen(patogeno)
        match = screen[screen["compound_name"].str.lower() == compuesto.lower().strip()]
        if not match.empty:
            row = match.iloc[0]
            smiles, nombre = row["smiles"], row["compound_name"]
            reused = float(row["pred_pmic"])
            fuente = "cribado precomputado"
    except FileNotFoundError:
        pass

    if reused is not None:
        pred = reused
    else:
        model, tokenizer, anchors = _get_model()
        from scripts.screen_repurposing import predict_pmic

        pred = predict_pmic(model, tokenizer, smiles, anchors[patogeno])

    return {
        "procedencia": "PREDICCION del modelo DTI+LoRA, no es una medida experimental",
        "compuesto": nombre,
        "patogeno": patogeno,
        "pmic_predicho": round(pred, 3),
        "error_tipico_pmic": 1.0,
        "fuente_del_valor": fuente,
        "aviso": (
            "Potencia fenotipica predicha sobre un ancla de organismo (GyrA). No es "
            "afinidad de union a la girasa ni prediccion de eficacia clinica. Error "
            "tipico ~1 unidad de pMIC: sirve para ordenar candidatos, no como valor "
            "absoluto por compuesto."
        ),
    }


def tool_consultar_cribado(patogeno: str, cubo: str | None = None, top: int = 10) -> dict:
    screen = load_screen(patogeno)
    if cubo:
        screen = screen[screen["bucket"] == cubo]
    columnas = [
        "compound_name", "compound_id", "screen", "bucket", "pred_pmic",
        "evidence_level", "is_hit_real", "px_real", "inhib_ave",
        "in_dti_test_split", "seen_in_training", "source_pathogen", "source_px",
    ]
    top_df = screen.nlargest(top, "pred_pmic")[[c for c in columnas if c in screen.columns]]
    return {
        "procedencia": "cribado precomputado (predicciones del DTI + evidencia real del dataset)",
        "patogeno": patogeno,
        "cubo": cubo or "todos",
        "n_total_en_cubo": int(len(screen)),
        "candidatos": top_df.to_dict("records"),
        "aviso": (
            "pred_pmic es una PREDICCION; px_real / inhib_ave / is_hit_real son MEDIDAS "
            "experimentales. No mezclarlas. 'seen_in_training' marca los compuestos que el "
            "modelo vio etiquetados al entrenar: para esos, acertar no demuestra capacidad "
            "predictiva."
        ),
    }


TOOL_IMPLS = {
    "retrieve_evidence": tool_retrieve_evidence,
    "predict_affinity": tool_predict_affinity,
    "consultar_cribado": tool_consultar_cribado,
}
