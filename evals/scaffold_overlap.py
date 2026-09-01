"""Fase 7 - Cuanto solapa quimicamente el hold-out con el entrenamiento.

    uv run python -m evals.scaffold_overlap

Fase 3 partio el dataset por InChIKey (un compuesto no puede estar a la vez en
train y test) y dejo el split por scaffold de Bemis-Murcko anotado como "variante
rigurosa para Fase 7". Rehacerlo exigiria reentrenar 8.6 h, que no cabe en el
calendario. Lo que SI cabe es medir cuanto optimismo introduce el split actual:
dos analogos del mismo esqueleto molecular pueden caer uno en train y otro en
test, y entonces el RMSE reportado mide interpolacion dentro de series quimicas
conocidas, no generalizacion a quimica nueva.

Esto convierte una sospecha en un numero. No arregla el split; lo acota.

Solo rdkit y CPU, sin GPU: se puede correr mientras el batch de predicciones
ocupa la tarjeta.
"""
from __future__ import annotations

import json

import pandas as pd
from rdkit import Chem, RDLogger
from rdkit.Chem.Scaffolds import MurckoScaffold

from app.config import settings
from app.generation.rag.corpus import _slug

RDLogger.DisableLog("rdApp.*")
OUTPUT = "evals/scaffold_overlap.json"


def _scaffold(smiles: str) -> str | None:
    """Esqueleto de Bemis-Murcko: la molecula sin cadenas laterales, que es lo
    que define una "serie quimica"."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        core = MurckoScaffold.GetScaffoldForMol(mol)
    except Exception:  # noqa: BLE001 - moleculas mal formadas, ya conocidas
        return None
    smi = Chem.MolToSmiles(core)
    return smi or None  # cadena vacia = molecula aciclica, sin esqueleto


def analyse(pathogen: str) -> dict:
    test_keys = set(
        json.loads(
            (settings.data_processed_dir / "split_test_inchikeys.json").read_text()
        )["inchikeys"][pathogen]
    )
    curated = pd.read_csv(settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv")

    # el LoRA v1 solo entreno con filas de potencia exacta o acotada
    entrenables = curated[curated["pX"].notna() & curated["relation"].notna()]
    compounds = entrenables.drop_duplicates("inchikey")[["inchikey", "smiles"]]
    compounds = compounds.assign(scaffold=compounds["smiles"].map(_scaffold))

    en_test = compounds["inchikey"].isin(test_keys)
    test_df = compounds[en_test]
    train_df = compounds[~en_test]

    train_scaffolds = set(train_df["scaffold"].dropna())
    test_con_scaffold = test_df[test_df["scaffold"].notna()]
    compartidos = test_con_scaffold["scaffold"].isin(train_scaffolds)

    return {
        "pathogen": pathogen,
        "compuestos_train": int(len(train_df)),
        "compuestos_test": int(len(test_df)),
        "test_sin_scaffold_aciclicos": int(test_df["scaffold"].isna().sum()),
        "test_con_scaffold": int(len(test_con_scaffold)),
        "test_que_comparten_scaffold_con_train": int(compartidos.sum()),
        "fraccion_compartida": round(float(compartidos.mean()), 4),
        "scaffolds_distintos_train": len(train_scaffolds),
        "scaffolds_distintos_test": int(test_con_scaffold["scaffold"].nunique()),
    }


def main() -> None:
    resultados = [analyse(p) for p in settings.pathogens]
    for r in resultados:
        print(
            f"{r['pathogen']}: {r['test_que_comparten_scaffold_con_train']}/"
            f"{r['test_con_scaffold']} compuestos del hold-out comparten esqueleto "
            f"con el entrenamiento ({r['fraccion_compartida']:.1%})"
        )
    with open(OUTPUT, "w") as fh:
        json.dump(resultados, fh, indent=2, ensure_ascii=False)
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
