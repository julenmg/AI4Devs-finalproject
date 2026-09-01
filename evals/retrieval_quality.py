"""Fase 7 - Calidad del retrieval del RAG.

    uv run python -m evals.retrieval_quality

Dos conjuntos de consultas, ninguno anotado a mano:

1. CONSULTAS DE COMPUESTO, con verdad de referencia POR CONSTRUCCION. Si se
   pregunta por un compuesto nombrado, el documento correcto se sabe: es su
   ficha `potency:<patogeno>:<inchikey>`. Con los 717 nombres del indice se
   generan consultas etiquetadas sin anotar nada.

   Se mide en DOS condiciones, y la segunda es la que informa de verdad:
     - `hibrido`   el sistema real (atajo lexico + busqueda semantica).
     - `semantico` solo busqueda vectorial, sin atajo.
   Reportar unicamente la primera seria propaganda: el atajo lexico esta
   disenado exactamente para clavar estas consultas, asi que su acierto no dice
   nada del embedding. La diferencia entre ambas es lo que aporta cada pieza.

2. CONSULTAS NO-COMPUESTO (mecanismos, literatura, metodologia), con ETIQUETA
   DEBIL por clase de evidencia: una pregunta sobre mecanismos de resistencia
   deberia traer `background` o `literature`, no fichas de potencia sueltas. Es
   un proxy, no relevancia documento a documento, y se reporta como tal:
   etiquetar relevancia a mano no cabe en el calendario de este proyecto.
"""
from __future__ import annotations

import json
import random

from app.generation.rag.retrieval import NAME_INDEX_PATH, detect_pathogen, retrieve
from app.generation.rag.store import search

OUTPUT = "evals/retrieval_quality.json"
SEED = 42
N_COMPOUND_QUERIES = 200
K = 8

PLANTILLAS = [
    "Que evidencia experimental hay sobre {n}?",
    "Que potencia tiene {n} frente a las bacterias del proyecto?",
    "Dame los datos de MIC de {n}.",
    "Es {n} un compuesto activo? Que dice la evidencia?",
    "Informacion sobre el compuesto {n}.",
]

# Consultas sin ficha de compuesto asociada. La etiqueta es la CLASE de evidencia
# que deberia aparecer en el top-k, no un documento concreto.
CONSULTAS_DEBILES = [
    ("Que mecanismos de resistencia a carbapenemicos tiene Klebsiella pneumoniae?",
     {"background", "literature"}),
    ("Que carbapenemasas predominan en Acinetobacter baumannii?", {"background", "literature"}),
    ("Que dice la literatura reciente sobre el tratamiento de infecciones por OXA-48?",
     {"literature"}),
    ("Que es el reposicionamiento de farmacos frente a la resistencia antimicrobiana?",
     {"literature", "background"}),
    ("Como se construyo el dataset curado de este proyecto?", {"methodology"}),
    ("Que umbral se uso para considerar que un compuesto es un hit?", {"methodology"}),
    ("Que puede y que no puede afirmar este sistema?", {"methodology"}),
    ("Cuantos compuestos se cribaron en total frente a Acinetobacter baumannii?",
     {"primary_screen_summary"}),
    ("Que proporcion de la quimioteca dio senal en el cribado primario?",
     {"primary_screen_summary"}),
    ("Hay compuestos con afinidad de union medida contra la carbapenemasa KPC?",
     {"binding_specific"}),
    ("Que inhibidores de beta-lactamasa OXA-48 tienen Kd reportado?", {"binding_specific"}),
    ("Que bombas de eflujo participan en la resistencia de A. baumannii?",
     {"background", "literature"}),
]


def _consultas_de_compuesto() -> list[dict]:
    nombres = json.loads(NAME_INDEX_PATH.read_text())
    # solo nombres con ficha inequivoca y suficientemente largos para no depender
    # de coincidencias triviales
    candidatos = [n for n, ids in nombres.items() if len(n) >= 6 and ids]
    rng = random.Random(SEED)
    elegidos = rng.sample(candidatos, min(N_COMPOUND_QUERIES, len(candidatos)))
    return [
        {
            "consulta": rng.choice(PLANTILLAS).format(n=nombre),
            "nombre": nombre,
            "gold": set(nombres[nombre]),
        }
        for nombre in elegidos
    ]


def _metricas(rankings: list[list[str]], golds: list[set[str]]) -> dict:
    p1 = rr = rec = 0.0
    for docs, gold in zip(rankings, golds):
        if docs and docs[0] in gold:
            p1 += 1
        if gold & set(docs):
            rec += 1
        for i, d in enumerate(docs, start=1):
            if d in gold:
                rr += 1 / i
                break
    n = len(golds)
    return {
        "n": n,
        "precision_at_1": round(p1 / n, 4),
        f"recall_at_{K}": round(rec / n, 4),
        "mrr": round(rr / n, 4),
    }


def evaluar_compuestos() -> dict:
    consultas = _consultas_de_compuesto()
    rank_hibrido, rank_semantico, golds = [], [], []

    for c in consultas:
        golds.append(c["gold"])
        rank_hibrido.append([h["doc_id"] for h in retrieve(c["consulta"], k=K)])

        # condicion semantica pura: se llama al store directamente, saltandose
        # el atajo lexico, con el mismo prefiltro de patogeno que usaria retrieve
        pathogen = detect_pathogen(c["consulta"])
        where = (
            {"$or": [{"pathogen": pathogen}, {"evidence_class": "methodology"}]}
            if pathogen
            else None
        )
        rank_semantico.append(
            [h["doc_id"] for h in search(c["consulta"], k=K, where=where)]
        )

    return {
        "hibrido_sistema_real": _metricas(rank_hibrido, golds),
        "semantico_sin_atajo_lexico": _metricas(rank_semantico, golds),
    }


def evaluar_clases() -> dict:
    aciertos, detalle = 0, []
    for consulta, esperadas in CONSULTAS_DEBILES:
        hits = retrieve(consulta, k=K)
        clases = [h["metadata"].get("evidence_class", "") for h in hits]
        ok = bool(set(clases) & esperadas)
        aciertos += ok
        detalle.append(
            {
                "consulta": consulta,
                "clases_esperadas": sorted(esperadas),
                "clases_recuperadas": sorted(set(clases)),
                "acierto": ok,
                "posicion_primera_esperada": next(
                    (i for i, c in enumerate(clases, 1) if c in esperadas), None
                ),
            }
        )
    return {
        "n": len(CONSULTAS_DEBILES),
        "consultas_con_la_clase_esperada_en_top_k": aciertos,
        "tasa": round(aciertos / len(CONSULTAS_DEBILES), 4),
        "detalle": detalle,
    }


def main() -> None:
    resultados = {
        "k": K,
        "consultas_de_compuesto": evaluar_compuestos(),
        "consultas_por_clase_etiqueta_debil": evaluar_clases(),
    }
    comp = resultados["consultas_de_compuesto"]
    print("--- consultas de compuesto (verdad por construccion) ---")
    for cond, m in comp.items():
        print(f"  {cond:28s} P@1 {m['precision_at_1']:.3f} | "
              f"R@{K} {m[f'recall_at_{K}']:.3f} | MRR {m['mrr']:.3f}  (n={m['n']})")
    clases = resultados["consultas_por_clase_etiqueta_debil"]
    print(f"\n--- consultas no-compuesto (etiqueta debil por clase) ---")
    print(f"  {clases['consultas_con_la_clase_esperada_en_top_k']}/{clases['n']} "
          f"traen la clase esperada en el top-{K} ({clases['tasa']:.1%})")
    for d in clases["detalle"]:
        if not d["acierto"]:
            print(f"    FALLA: {d['consulta'][:60]} -> {d['clases_recuperadas']}")

    with open(OUTPUT, "w") as fh:
        json.dump(resultados, fh, indent=2, ensure_ascii=False)
    print(f"\n-> {OUTPUT}")


if __name__ == "__main__":
    main()
