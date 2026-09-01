"""Fase 7 - Predicciones de los tres checkpoints sobre el hold-out COMPLETO.

    uv run python -m evals.predict_holdout

Es el camino largo de la fase (~3.8 h en la GTX 1070), asi que hace UNA sola
cosa: predecir y cachear. Todo el analisis (RMSE, test pareado, correlaciones,
suelo de ruido) se hace despues sobre el CSV, sin volver a tocar la GPU.

Por que una prediccion por COMPUESTO y no por fila: la entrada del modelo es
(SMILES, ancla de organismo) y el ancla es fija por patogeno, asi que todas las
filas del mismo compuesto dan exactamente la misma prediccion. El hold-out son
8 023 filas exactas pero solo 3 532 compuestos unicos: predecir por fila seria
repetir el 56% del trabajo para nada.

Por que sin batching: medido a batch 1/4/8 sobre la 1070 da 1.297 / 1.280 /
1.311 s por compuesto. No hay ganancia — el modelo es compute-bound a seq 1512,
igual que ya se observo entrenando en Fase 3.

Los tres checkpoints:
  - `baseline`  el checkpoint de IBM SIN LoRA (referencia de mejora).
  - `step5000`  el elegido en Fase 3 por early-stopping (RMSE 0.985 sobre el
                subset de 132 filas exactas).
  - `final`     el ultimo del entrenamiento (1.010 sobre ese mismo subset).
Fase 3 dejo explicitamente pendiente reconfirmar esa eleccion sobre el hold-out
completo y por patogeno; esto es lo que salda esa deuda.
"""
from __future__ import annotations

import json
import time

import pandas as pd
import torch
from peft import PeftModel

from app.config import settings
from app.foundation.dti_model import (
    NORM_Y_MEAN,
    NORM_Y_STD,
    _build_encoder_input,
    load_model,
)
from app.generation.rag.corpus import _slug
from mammal.keys import SCALARS_PREDICTION_HEAD_LOGITS
from training.lora_finetune import _UnusedHeadStub, _fetch_gyra_sequence

CHECKPOINTS = {
    "baseline": None,
    "step5000": "training/output/lora_adapter_step5000",
    "final": "training/output/lora_adapter",
}
OUTPUT = "evals/holdout_predictions.csv"


def holdout_compounds(pathogen: str) -> pd.DataFrame:
    """Compuestos unicos del hold-out con al menos una medida EXACTA.

    Se agrega por InChIKey quedandose con la mediana de pX (robusta a un valor
    discordante) y se guarda la dispersion intra-compuesto: esas replicas son el
    suelo de ruido experimental con el que hay que comparar el RMSE del modelo.
    """
    test_keys = set(
        json.loads(
            (settings.data_processed_dir / "split_test_inchikeys.json").read_text()
        )["inchikeys"][pathogen]
    )
    curated = pd.read_csv(settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv")
    exact = curated[curated["inchikey"].isin(test_keys) & (curated["relation"] == "=")]

    grouped = exact.groupby("inchikey").agg(
        smiles=("smiles", "first"),
        px_median=("pX", "median"),
        px_std=("pX", "std"),
        px_min=("pX", "min"),
        px_max=("pX", "max"),
        n_medidas=("pX", "size"),
        is_hit=("is_hit", "any"),
    )
    return grouped.reset_index().assign(pathogen=pathogen)


def _load(adapter: str | None):
    model, tokenizer = load_model()
    model.encoder_head = _UnusedHeadStub()
    if adapter:
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return model, tokenizer


def main() -> None:
    started = time.time()
    frames = [holdout_compounds(p) for p in settings.pathogens]
    compounds = pd.concat(frames, ignore_index=True)
    anchors = {p: _fetch_gyra_sequence(p) for p in settings.pathogens}
    total = len(compounds) * len(CHECKPOINTS)
    print(f"[fase7] {len(compounds)} compuestos x {len(CHECKPOINTS)} checkpoints "
          f"= {total} predicciones (~{total * 1.3 / 3600:.1f} h)", flush=True)
    for p in settings.pathogens:
        n = int((compounds["pathogen"] == p).sum())
        print(f"[fase7]   {p}: {n} compuestos", flush=True)

    rows = []
    for name, adapter in CHECKPOINTS.items():
        print(f"\n[fase7] checkpoint '{name}' ({adapter or 'sin LoRA'})", flush=True)
        model, tokenizer = _load(adapter)
        t0 = time.time()
        for i, row in enumerate(compounds.itertuples(), start=1):
            sample = _build_encoder_input(
                smiles=row.smiles,
                protein_sequence=anchors[row.pathogen],
                tokenizer_op=tokenizer,
                device=model.device,
            )
            with torch.no_grad():
                out = model.forward_encoder_only([sample])
            pred = float(out[SCALARS_PREDICTION_HEAD_LOGITS][:, 0] * NORM_Y_STD + NORM_Y_MEAN)
            rows.append(
                {
                    "checkpoint": name,
                    "pathogen": row.pathogen,
                    "inchikey": row.inchikey,
                    "pred_pmic": round(pred, 4),
                    "px_median": round(row.px_median, 4),
                    "px_std": None if pd.isna(row.px_std) else round(row.px_std, 4),
                    "px_min": round(row.px_min, 4),
                    "px_max": round(row.px_max, 4),
                    "n_medidas": int(row.n_medidas),
                    "is_hit": bool(row.is_hit),
                }
            )
            if i % 250 == 0:
                ritmo = (time.time() - t0) / i
                print(f"  {i}/{len(compounds)} ({ritmo:.2f} s/compuesto, "
                      f"quedan ~{(len(compounds) - i) * ritmo / 60:.0f} min)", flush=True)

        # se vuelca en cada checkpoint: si el proceso muere, no se pierde lo hecho
        pd.DataFrame(rows).to_csv(OUTPUT, index=False)
        print(f"[fase7] '{name}' listo en {(time.time() - t0) / 60:.1f} min "
              f"-> {OUTPUT} ({len(rows)} filas)", flush=True)
        del model
        torch.cuda.empty_cache()
        load_model.cache_clear()

    print(f"\n[fase7] predicciones completas en {(time.time() - started) / 3600:.2f} h",
          flush=True)


if __name__ == "__main__":
    main()
