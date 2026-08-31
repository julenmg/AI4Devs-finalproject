"""Fase 6 - Cribado precomputado del caso de reposicionamiento.

    uv run python -m scripts.screen_repurposing

Por que es un batch y no algo que haga el agente en vivo: una prediccion cuesta
~1.06 s en la GTX 1070, y el cribado son ~1500. Veinticinco minutos de espera es
la diferencia entre que se vea el sistema y que no se vea. El agente consume
este CSV; `predict_affinity` en vivo queda para consultas de un compuesto suelto.

El CSV resultante se versiona en git (excepcion documentada en .gitignore, mismo
patron que el adapter LoRA de Fase 3 y los JSON de PubMed): es el entregable del
caso de estudio, pesa poco, y asi la demo funciona sin GPU disponible.

AISLAMIENTO DEL DTI: en este bucle no hay LLM. Se leen SMILES del dataset curado
y se llama al modelo. La prediccion no puede contaminarse con la evidencia que
el RAG recuperaria, porque el RAG no interviene aqui.
"""
from __future__ import annotations

import argparse
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
from app.generation.agentic.screening import (
    assign_bucket,
    build_screen_a,
    build_screen_b,
)
from app.ingestion.curate_dataset import HIT_PX_CUTOFF
from app.generation.rag.corpus import _slug
from mammal.keys import SCALARS_PREDICTION_HEAD_LOGITS
from training.lora_finetune import _UnusedHeadStub, _fetch_gyra_sequence

DEFAULT_ADAPTER = "training/output/lora_adapter_step5000"
OUTPUT_NAME = "repurposing_screen_{slug}.csv"


def load_finetuned(adapter: str = DEFAULT_ADAPTER):
    """Modelo base + adapter LoRA de Fase 3, en modo inferencia.

    Se replica el mismo montaje del entrenamiento (`encoder_head` neutralizada)
    porque el adapter se guardo sobre esa estructura; sin ello, las claves de
    los pesos no encajarian.
    """
    model, tokenizer = load_model()
    model.encoder_head = _UnusedHeadStub()
    model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return model, tokenizer


def predict_pmic(model, tokenizer, smiles: str, anchor: str) -> float:
    """pMIC predicho para un compuesto contra el ancla de organismo.

    RECORDATORIO DE ENCUADRE: el ancla es la GyrA del patogeno y es un requisito
    de arquitectura del checkpoint (rellenar el slot de proteina), NO una
    afirmacion de union a la girasa. La salida es potencia fenotipica, no
    afinidad de union ni eficacia clinica.
    """
    sample = _build_encoder_input(
        smiles=smiles, protein_sequence=anchor, tokenizer_op=tokenizer, device=model.device
    )
    with torch.no_grad():
        out = model.forward_encoder_only([sample])
    return float(out[SCALARS_PREDICTION_HEAD_LOGITS][:, 0] * NORM_Y_STD + NORM_Y_MEAN)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cribado de reposicionamiento (Fase 6)")
    parser.add_argument("--adapter", default=DEFAULT_ADAPTER)
    parser.add_argument("--limit", type=int, help="corta el numero de candidatos (pruebas)")
    parser.add_argument("--skip-transfer", action="store_true", help="solo el cribado A")
    args = parser.parse_args()

    started = time.time()
    print("[fase6] cargando checkpoint base + adapter LoRA...", flush=True)
    model, tokenizer = load_finetuned(args.adapter)
    print(f"[fase6] modelo listo en {time.time() - started:.0f}s (device {model.device})", flush=True)

    for pathogen in settings.pathogens:
        anchor = _fetch_gyra_sequence(pathogen)
        candidates = build_screen_a(pathogen)
        if not args.skip_transfer:
            candidates += build_screen_b(pathogen)
        if args.limit:
            candidates = candidates[: args.limit]

        print(f"\n[fase6] {pathogen}: {len(candidates)} candidatos "
              f"(ancla GyrA {len(anchor)} aa)", flush=True)

        rows = []
        t0 = time.time()
        for i, cand in enumerate(candidates, start=1):
            pred = predict_pmic(model, tokenizer, cand.smiles, anchor)
            rows.append({**vars(cand), "pred_pmic": round(pred, 4)})
            if i % 100 == 0:
                ritmo = (time.time() - t0) / i
                print(f"  {i}/{len(candidates)} ({ritmo:.2f} s/compuesto, "
                      f"quedan ~{(len(candidates) - i) * ritmo / 60:.1f} min)", flush=True)

        df = pd.DataFrame(rows)
        df["notes"] = df["notes"].map(lambda v: "; ".join(v) if isinstance(v, list) else "")
        # Umbral de "prediccion alta" = el MISMO HIT_PX_CUTOFF (5.0) con el que la
        # curacion de Fase 1 definio un hit. Se reutiliza en vez de elegir uno
        # ahora: fijarlo mirando como quedan los cubos seria elegir el resultado.
        df["bucket"] = df.apply(lambda r: assign_bucket(r, HIT_PX_CUTOFF), axis=1)
        df = df.sort_values("pred_pmic", ascending=False)
        out = settings.data_processed_dir / OUTPUT_NAME.format(slug=_slug(pathogen))
        df.to_csv(out, index=False)
        print(f"[fase6] {pathogen}: {len(df)} predicciones -> {out} "
              f"({time.time() - t0:.0f}s)", flush=True)

    print(f"\n[fase6] cribado completo en {(time.time() - started) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
