"""Fase 7 - Analisis del hold-out: RMSE, correlaciones, test pareado y contexto.

    uv run python -m evals.analyze_holdout

Consume `evals/holdout_predictions.csv` (ya calculado en GPU) y no vuelve a
tocar el modelo. Cuatro bloques:

1. METRICAS POR CHECKPOINT Y POR PATOGENO. Fase 3 las reporto mezcladas y sobre
   un subset de 132 filas exactas; aqui van separadas y sobre el hold-out
   completo, que es la deuda que dejo abierta.

2. COMPARACION PAREADA step5000 vs final. Los dos checkpoints predicen LOS
   MISMOS compuestos, asi que comparar dos RMSE independientes desperdicia esa
   estructura. Se comparan los errores cuadraticos compuesto a compuesto
   (bootstrap sobre las diferencias + Wilcoxon), que es mucho mas potente. Si el
   intervalo de la diferencia incluye el cero, la conclusion honesta es
   "indistinguibles" y se dice tal cual.

3. SUELO DE RUIDO EXPERIMENTAL. Los compuestos con varias medidas exactas en el
   hold-out permiten estimar cuanto se contradice el propio experimento consigo
   mismo. Sin esa referencia, un RMSE de 1.0 no significa nada: no se sabe si
   esta lejos o cerca de lo que el dato permite.

4. RMSE POR SOLAPE DE SCAFFOLD. El split de Fase 3 es por InChIKey, asi que dos
   analogos del mismo esqueleto pueden caer uno en train y otro en test. Separar
   el hold-out en "comparte esqueleto con el entrenamiento" vs "esqueleto nuevo"
   convierte esa limitacion conocida en dos numeros, sin reentrenar.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from rdkit import Chem, RDLogger
from rdkit.Chem.Scaffolds import MurckoScaffold
from scipy.stats import pearsonr, spearmanr, wilcoxon

from app.config import settings
from app.generation.rag.corpus import _slug

RDLogger.DisableLog("rdApp.*")

PREDICTIONS = "evals/holdout_predictions.csv"
OUTPUT = "evals/holdout_metrics.json"
SEED = 42
N_BOOTSTRAP = 10_000


def _rmse(err: np.ndarray) -> float:
    return float(np.sqrt(np.mean(err**2)))


def metricas(df: pd.DataFrame) -> dict:
    err = df["pred_pmic"].to_numpy() - df["px_median"].to_numpy()
    out = {
        "n": int(len(df)),
        "rmse": round(_rmse(err), 4),
        "mae": round(float(np.mean(np.abs(err))), 4),
        "sesgo": round(float(np.mean(err)), 4),
        "std_pred": round(float(df["pred_pmic"].std()), 4),
        "std_real": round(float(df["px_median"].std()), 4),
    }
    if len(df) > 2 and df["px_median"].nunique() > 1:
        out["spearman"] = round(float(spearmanr(df["px_median"], df["pred_pmic"]).statistic), 4)
        out["pearson"] = round(float(pearsonr(df["px_median"], df["pred_pmic"]).statistic), 4)
    hits, no_hits = df[df["is_hit"]], df[~df["is_hit"]]
    if len(hits) and len(no_hits):
        out["pred_media_hits"] = round(float(hits["pred_pmic"].mean()), 4)
        out["pred_media_no_hits"] = round(float(no_hits["pred_pmic"].mean()), 4)
        out["separacion"] = round(out["pred_media_hits"] - out["pred_media_no_hits"], 4)
    return out


def comparacion_pareada(a: pd.DataFrame, b: pd.DataFrame, nombre_a: str, nombre_b: str) -> dict:
    """Bootstrap sobre la diferencia de error cuadratico por compuesto."""
    merged = a.merge(b, on=["pathogen", "inchikey"], suffixes=("_a", "_b"))
    err_a = (merged["pred_pmic_a"] - merged["px_median_a"]).to_numpy()
    err_b = (merged["pred_pmic_b"] - merged["px_median_b"]).to_numpy()
    dif = err_a**2 - err_b**2  # negativo => a mejor que b

    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(dif), size=(N_BOOTSTRAP, len(dif)))
    boot_rmse_a = np.sqrt(np.mean((err_a**2)[idx], axis=1))
    boot_rmse_b = np.sqrt(np.mean((err_b**2)[idx], axis=1))
    boot_dif = boot_rmse_a - boot_rmse_b
    lo, hi = np.percentile(boot_dif, [2.5, 97.5])

    try:
        p_valor = float(wilcoxon(err_a**2, err_b**2).pvalue)
    except ValueError:  # todas las diferencias son cero
        p_valor = 1.0

    return {
        "comparacion": f"{nombre_a} vs {nombre_b}",
        "n_compuestos": int(len(merged)),
        f"rmse_{nombre_a}": round(_rmse(err_a), 4),
        f"rmse_{nombre_b}": round(_rmse(err_b), 4),
        "diferencia_rmse": round(_rmse(err_a) - _rmse(err_b), 4),
        "ic95_diferencia": [round(float(lo), 4), round(float(hi), 4)],
        "wilcoxon_p": round(p_valor, 5),
        "indistinguibles": bool(lo <= 0 <= hi),
    }


def suelo_de_ruido(df: pd.DataFrame) -> dict:
    """Dispersion entre medidas del MISMO compuesto: el suelo por debajo del
    cual ningun modelo puede bajar, porque el propio dato no lo soporta."""
    rep = df[df["n_medidas"] > 1].drop_duplicates(["pathogen", "inchikey"])
    if rep.empty:
        return {}
    # desviacion tipica de la media de k medidas ~ std/sqrt(k); como comparacion
    # conservadora se usa la std intra-compuesto tal cual
    return {
        "compuestos_con_replicas": int(len(rep)),
        "std_intra_media": round(float(rep["px_std"].mean()), 4),
        "rango_intra_medio_log": round(float((rep["px_max"] - rep["px_min"]).mean()), 4),
        "rango_intra_mediano_log": round(float((rep["px_max"] - rep["px_min"]).median()), 4),
        "nota": (
            "Dispersion entre medidas del mismo compuesto en el propio dataset. "
            "Un RMSE del modelo por debajo de este valor no seria creible: "
            "significaria predecir mejor de lo que el experimento se reproduce."
        ),
    }


def _scaffold(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        return Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(mol)) or None
    except Exception:  # noqa: BLE001
        return None


def por_solape_de_scaffold(preds: pd.DataFrame) -> dict:
    """RMSE separando el hold-out en quimica conocida vs quimica nueva."""
    test_keys = json.loads(
        (settings.data_processed_dir / "split_test_inchikeys.json").read_text()
    )["inchikeys"]

    marcas = {}
    for pathogen in settings.pathogens:
        curated = pd.read_csv(settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv")
        entrenables = curated[curated["pX"].notna() & curated["relation"].notna()]
        comp = entrenables.drop_duplicates("inchikey")[["inchikey", "smiles"]]
        comp = comp.assign(scaffold=comp["smiles"].map(_scaffold))
        en_test = comp["inchikey"].isin(set(test_keys[pathogen]))
        train_scaffolds = set(comp.loc[~en_test, "scaffold"].dropna())
        for row in comp[en_test].itertuples():
            if row.scaffold is None:
                continue
            marcas[(pathogen, row.inchikey)] = row.scaffold in train_scaffolds

    preds = preds.copy()
    preds["scaffold_conocido"] = [
        marcas.get((p, k)) for p, k in zip(preds["pathogen"], preds["inchikey"])
    ]
    con_marca = preds[preds["scaffold_conocido"].notna()]

    resultado = {}
    for checkpoint, grupo in con_marca.groupby("checkpoint"):
        conocido = grupo[grupo["scaffold_conocido"]]
        nuevo = grupo[~grupo["scaffold_conocido"].astype(bool)]
        resultado[checkpoint] = {
            "esqueleto_visto_en_train": metricas(conocido) if len(conocido) else {},
            "esqueleto_nuevo": metricas(nuevo) if len(nuevo) else {},
        }
    return resultado


def main() -> None:
    preds = pd.read_csv(PREDICTIONS)
    checkpoints = list(preds["checkpoint"].unique())
    print(f"checkpoints en el fichero: {checkpoints}")

    resultados: dict = {"por_checkpoint": {}, "pareadas": [], "suelo_de_ruido": {}}

    for checkpoint in checkpoints:
        sub = preds[preds["checkpoint"] == checkpoint]
        resultados["por_checkpoint"][checkpoint] = {
            "global": metricas(sub),
            **{p: metricas(sub[sub["pathogen"] == p]) for p in settings.pathogens},
        }

    for a, b in (("step5000", "final"), ("step5000", "baseline"), ("final", "baseline")):
        if a in checkpoints and b in checkpoints:
            resultados["pareadas"].append(
                comparacion_pareada(
                    preds[preds["checkpoint"] == a], preds[preds["checkpoint"] == b], a, b
                )
            )

    resultados["suelo_de_ruido"] = suelo_de_ruido(preds[preds["checkpoint"] == checkpoints[0]])
    resultados["por_solape_de_scaffold"] = por_solape_de_scaffold(preds)

    # ------------------------------------------------------------- informe
    print("\n=== RMSE por checkpoint y patogeno ===")
    for checkpoint, bloques in resultados["por_checkpoint"].items():
        print(f"\n{checkpoint}:")
        for ambito, m in bloques.items():
            if not m or not m.get("n"):
                continue  # patogeno sin compuestos en este subconjunto
            print(f"  {ambito[:26]:26s} n={m['n']:5d} RMSE {m['rmse']:.3f} "
                  f"MAE {m['mae']:.3f} sesgo {m['sesgo']:+.3f} "
                  f"Spearman {m.get('spearman', float('nan')):+.3f}")

    print("\n=== comparaciones pareadas ===")
    for c in resultados["pareadas"]:
        veredicto = "INDISTINGUIBLES" if c["indistinguibles"] else "diferencia significativa"
        print(f"  {c['comparacion']:22s} dif RMSE {c['diferencia_rmse']:+.4f} "
              f"IC95 [{c['ic95_diferencia'][0]:+.4f}, {c['ic95_diferencia'][1]:+.4f}] "
              f"p={c['wilcoxon_p']:.4g} -> {veredicto}")

    if resultados["suelo_de_ruido"]:
        s = resultados["suelo_de_ruido"]
        print(f"\n=== suelo de ruido experimental ===")
        print(f"  {s['compuestos_con_replicas']} compuestos con replicas | "
              f"std intra {s['std_intra_media']:.3f} | "
              f"rango medio {s['rango_intra_medio_log']:.3f} log")

    print("\n=== RMSE por solape de esqueleto ===")
    for checkpoint, bloque in resultados["por_solape_de_scaffold"].items():
        conocido = bloque["esqueleto_visto_en_train"]
        nuevo = bloque["esqueleto_nuevo"]
        if conocido and nuevo:
            print(f"  {checkpoint:10s} conocido n={conocido['n']:5d} RMSE {conocido['rmse']:.3f} "
                  f"| nuevo n={nuevo['n']:5d} RMSE {nuevo['rmse']:.3f} "
                  f"| penalizacion {nuevo['rmse'] - conocido['rmse']:+.3f}")

    with open(OUTPUT, "w") as fh:
        json.dump(resultados, fh, indent=2, ensure_ascii=False)
    print(f"\n-> {OUTPUT}")


if __name__ == "__main__":
    main()
