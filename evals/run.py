"""Fase 7 - Agrega los resultados de todas las evaluaciones en un solo fichero.

    uv run python -m evals.run

No calcula nada: cada evaluacion se ejecuta por separado (algunas cuestan horas
de GPU) y vuelca su propio JSON. Esto los reune en `evals/results.json`, que es
lo que se cita en el README, para que las cifras del documento y las de los
ficheros no puedan divergir.

Orden de ejecucion recomendado (la GPU es el cuello de botella y NO conviene
solapar: dos contextos CUDA en la misma tarjeta se reparten por time-slicing y
duplican el tiempo del camino critico, medido en esta fase):

    uv run python -m evals.predict_holdout    # ~3.8 h GPU
    uv run python -m evals.analyze_holdout    # segundos, CPU
    uv run python -m evals.binding_check      # ~1.5 min GPU
    uv run python -m evals.scaffold_overlap   # ~1 min CPU
    uv run python -m evals.retrieval_quality  # ~2 min
    uv run python -m evals.hallucination      # ~35 min (API)
    uv run python -m evals.run
"""
from __future__ import annotations

import json
from pathlib import Path

FUENTES = {
    "modelo_dti": "evals/holdout_metrics.json",
    "binding_verificacion": "evals/binding_check.json",
    "solape_scaffolds": "evals/scaffold_overlap.json",
    "retrieval": "evals/retrieval_quality.json",
    "anti_invencion": "evals/hallucination.json",
}
OUTPUT = "evals/results.json"

# Claves demasiado voluminosas para el resumen agregado; se quedan en su fichero.
PODAR = {"detalle", "detalle_por_compuesto"}


def _cargar(path: str) -> dict | list | None:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k not in PODAR}
    return data


def main() -> None:
    resultados, faltan = {}, []
    for nombre, path in FUENTES.items():
        data = _cargar(path)
        if data is None:
            faltan.append(f"{nombre} ({path})")
        else:
            resultados[nombre] = data

    resultados["_pendientes"] = faltan
    Path(OUTPUT).write_text(json.dumps(resultados, indent=2, ensure_ascii=False))

    print(f"agregadas {len(resultados) - 1}/{len(FUENTES)} evaluaciones -> {OUTPUT}")
    for f in faltan:
        print(f"  PENDIENTE: {f}")


if __name__ == "__main__":
    main()
