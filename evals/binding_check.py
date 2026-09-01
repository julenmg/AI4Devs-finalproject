"""Fase 7 - Las 66 filas de binding real: que comprueban y que NO.

    uv run python -m evals.binding_check

Estas filas (53 Kp + 13 Ab) son Ki/Kd medidos contra una proteina diana concreta
—las carbapenemasas y beta-lactamasas del propio proyecto: KPC, OXA-48,
metalo-beta-lactamasa (NDM), SHV, ADC, UDP-galactopiranosa mutasa— y se apartaron
desde la Fase 1 sin entrar jamas en el fine-tune.

LO QUE ESTO NO ES: una metrica de rendimiento. El modelo es un QSAR FENOTIPICO
(SMILES -> pMIC sobre un ancla de organismo) y estas filas son AFINIDAD DE UNION
a una proteina aislada. Son magnitudes distintas, no dos estimaciones de lo
mismo. Un "RMSE de binding" no se puede calcular y no se calcula; con n=66
tampoco habria potencia estadistica aunque fuesen comparables.

LO QUE SI COMPRUEBAN, y por eso se reservaron:

1. LA FRONTERA, con datos en lugar de con una afirmacion. Se predice pMIC para
   los 66 compuestos y se correlaciona con su pKi/pKd real. Una correlacion
   cercana a CERO es el resultado BUENO: demuestra empiricamente que el sistema
   no esta prediciendo afinidad de union, que es justo lo que el README lleva
   seis fases afirmando. Una correlacion alta obligaria a explicarla, no a
   celebrarla.
2. ROBUSTEZ FUERA DE DISTRIBUCION. Son inhibidores de beta-lactamasa,
   quimicamente distintos del grueso del corpus: se comprueba que el modelo no
   devuelve valores absurdos ni degenerados sobre ellos.
3. Sirven de entrada para la comprobacion anti-trampa de la Fase 6 (que el
   agente presente prediccion y medida por separado), que se ejecuta aparte.
"""
from __future__ import annotations

import json

import pandas as pd
import torch
from peft import PeftModel
from scipy.stats import pearsonr, spearmanr

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

ADAPTER = "training/output/lora_adapter_step5000"
OUTPUT = "evals/binding_check.json"


def main() -> None:
    targets_path = settings.data_raw_dir / "chembl_targets.json"
    targets = json.loads(targets_path.read_text()) if targets_path.exists() else {}

    model, tokenizer = load_model()
    model.encoder_head = _UnusedHeadStub()
    model = PeftModel.from_pretrained(model, ADAPTER)
    model.eval()

    filas = []
    for pathogen in settings.pathogens:
        path = settings.data_processed_dir / f"verification_binding_{_slug(pathogen)}.csv"
        df = pd.read_csv(path)
        anchor = _fetch_gyra_sequence(pathogen)
        for row in df.itertuples():
            sample = _build_encoder_input(
                smiles=row.smiles,
                protein_sequence=anchor,
                tokenizer_op=tokenizer,
                device=model.device,
            )
            with torch.no_grad():
                out = model.forward_encoder_only([sample])
            pred = float(out[SCALARS_PREDICTION_HEAD_LOGITS][:, 0] * NORM_Y_STD + NORM_Y_MEAN)
            tinfo = targets.get(str(row.target_chembl_id), {})
            filas.append(
                {
                    "pathogen": pathogen,
                    "compound_id": row.compound_id,
                    "target": tinfo.get("pref_name") or str(row.target_chembl_id),
                    "medida": row.assay_measure,
                    "p_binding_real": round(float(row.pX), 4),
                    "pmic_predicho": round(pred, 4),
                    "relation": row.relation,
                }
            )

    res = pd.DataFrame(filas)
    exactas = res[res["relation"] == "="]

    correlaciones = {}
    if len(exactas) > 2:
        correlaciones = {
            "n_exactas": int(len(exactas)),
            "spearman": round(
                float(spearmanr(exactas["p_binding_real"], exactas["pmic_predicho"]).statistic), 4
            ),
            "spearman_p": round(
                float(spearmanr(exactas["p_binding_real"], exactas["pmic_predicho"]).pvalue), 4
            ),
            "pearson": round(
                float(pearsonr(exactas["p_binding_real"], exactas["pmic_predicho"]).statistic), 4
            ),
        }

    resultado = {
        "n_filas": int(len(res)),
        "por_patogeno": res["pathogen"].value_counts().to_dict(),
        "dianas": res["target"].value_counts().to_dict(),
        "p_binding_real": {
            "media": round(float(res["p_binding_real"].mean()), 4),
            "std": round(float(res["p_binding_real"].std()), 4),
            "rango": [round(float(res["p_binding_real"].min()), 4),
                      round(float(res["p_binding_real"].max()), 4)],
        },
        "pmic_predicho": {
            "media": round(float(res["pmic_predicho"].mean()), 4),
            "std": round(float(res["pmic_predicho"].std()), 4),
            "rango": [round(float(res["pmic_predicho"].min()), 4),
                      round(float(res["pmic_predicho"].max()), 4)],
        },
        "correlacion_binding_vs_prediccion": correlaciones,
        "interpretacion": (
            "Una correlacion cercana a cero es el resultado ESPERADO Y BUENO: "
            "confirma con datos que el modelo no predice afinidad de union, solo "
            "potencia fenotipica. Estas 66 filas NO son una metrica de rendimiento "
            "del modelo y no se debe derivar de ellas ningun RMSE de binding."
        ),
        "detalle": filas,
    }

    print(f"filas: {resultado['n_filas']} | dianas: {len(resultado['dianas'])}")
    print(f"pKi/pKd real   media {resultado['p_binding_real']['media']:.2f} "
          f"rango {resultado['p_binding_real']['rango']}")
    print(f"pMIC predicho  media {resultado['pmic_predicho']['media']:.2f} "
          f"rango {resultado['pmic_predicho']['rango']}")
    if correlaciones:
        print(f"correlacion binding real vs pMIC predicho: "
              f"Spearman {correlaciones['spearman']:+.3f} (p={correlaciones['spearman_p']:.3f}) | "
              f"Pearson {correlaciones['pearson']:+.3f}")

    with open(OUTPUT, "w") as fh:
        json.dump(resultado, fh, indent=2, ensure_ascii=False)
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
