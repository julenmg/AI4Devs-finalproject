"""Fase 1 - Cruza ChEMBL + CO-ADD, limpia y arma el dataset curado
(positivos y negativos reales) que alimenta el fine-tune de la Fase 3.

Encuadre explicito (decision documentada en docs/decisions.md, Fase 1):
este dataset es un QSAR de POTENCIA FENOTIPICA (SMILES -> pMIC), no un
dataset de binding fármaco-diana especifico. ~98% de las filas de ChEMBL
son MIC/IC50/EC50 de celula completa (el "target" es el organismo, no una
proteina), asi que tratarlas como afinidad de union real romperia la
frontera que marca el CLAUDE.md del proyecto. Las pocas filas que SI son
binding real contra una diana molecular concreta (standard_type Ki/Kd) se
apartan en un fichero de verificacion aparte para la Fase 7 - no entran en
el fine-tune principal ni se mezclan con el resto.

pMIC/pIC50/pEC50 = -log10(valor convertido a molar). El % de inhibicion a
concentracion unica de CO-ADD NO se convierte a un pseudo-IC50 (un solo
punto no permite derivar una curva sin asumir una pendiente de Hill
arbitraria); se usa tal cual, como etiqueta binaria activo/inactivo al
umbral de hit propio de CO-ADD (INHIB_AVE >= 80).

Salidas en data/processed/, todas por patogeno:
- curated_<patogeno>.csv               dataset principal QSAR (SMILES->pMIC)
- verification_binding_<patogeno>.csv  Ki/Kd reales, aparte, para Fase 7
- discrepancies_dose_response_duplicates_<patogeno>.csv
- discrepancies_chembl_coadd_<patogeno>.csv
"""
from __future__ import annotations

import random
import re

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import AllChem, Descriptors
from rdkit.Chem.inchi import MolToInchiKey

from app.config import settings

RDLogger.DisableLog("rdApp.*")  # los warnings de valencia de RDKit ya se han
# revisado a mano (moleculas con boro mal formadas); no hace falta que
# inunden el log en cada corrida.

RANDOM_SEED = 42
HIT_PX_CUTOFF = 5.0  # ~10 uM o mas potente; convencion habitual de "hit" en
# cribado antibacteriano temprano.
COADD_HIT_INHIB_THRESHOLD = 80.0  # umbral de hit propio de CO-ADD.
TRIVIAL_INHIB_CEILING = 25.0
TRIVIAL_TANIMOTO_CEILING = 0.4
TRIVIAL_UNDERSAMPLE_RATIO = 20  # max. negativos triviales por fila no-trivial
CROSS_SOURCE_LOG_DIFF_THRESHOLD = 2.0  # 2 ordenes de magnitud
DUPLICATE_LOG_DIFF_TOLERANCE = 0.3  # ~2x, variacion normal entre replicas

MORGAN_RADIUS = 2
MORGAN_NBITS = 2048

BINDING_STANDARD_TYPES = {"Ki", "Kd"}

# value -> factor multiplicativo directo a Molar (no depende del MW)
_MOLAR_UNIT_FACTORS = {
    "M": 1.0,
    "mM": 1e-3,
    "uM": 1e-6,
    "nM": 1e-9,
    "pM": 1e-12,
}
# value en estas unidades depende del peso molecular del compuesto:
# M = (valor_ug_por_mL * 1e-3) / MW_g_por_mol
_MASS_PER_VOLUME_UNITS = {"ug.mL-1", "ug/mL", "microg/cm3"}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _mol_from_smiles(smiles: str) -> Chem.Mol | None:
    if not isinstance(smiles, str) or not smiles:
        return None
    return Chem.MolFromSmiles(smiles)


def _to_molar(value: float, unit: str, mol: Chem.Mol | None) -> float | None:
    if unit in _MOLAR_UNIT_FACTORS:
        return value * _MOLAR_UNIT_FACTORS[unit]
    if unit in _MASS_PER_VOLUME_UNITS:
        if mol is None:
            return None
        mw = Descriptors.MolWt(mol)
        if not mw:
            return None
        return value * 1e-3 / mw
    return None  # unidad no reconocida (ppm, "ug" sin volumen, etc.) -> se descarta


def _px(value_molar: float | None) -> float | None:
    if value_molar is None or value_molar <= 0:
        return None
    return -np.log10(value_molar)


_RELATIONAL_VALUE_RE = re.compile(r"^(<=|>=|<|>)?\s*([-+]?[0-9]*\.?[0-9]+)$")


def _parse_relational_value(raw) -> tuple[str | None, float | None]:
    """CO-ADD embebe el operador dentro del propio valor (p.ej. '>10'), a
    diferencia de ChEMBL que lo trae en una columna aparte. Se parsea aqui
    para tratarlo exactamente igual que standard_relation."""
    if isinstance(raw, (int, float)):
        return "=", float(raw)
    if not isinstance(raw, str):
        return None, None
    match = _RELATIONAL_VALUE_RE.match(raw.strip())
    if not match:
        return None, None
    relation, value = match.groups()
    return (relation or "="), float(value)


def _resolve_is_hit(px: float | None, relation: str | None) -> bool:
    """'>'/'>=' en una medida de concentracion (MIC/IC50/EC50) significa que
    el compuesto NO hizo efecto hasta esa dosis -> nunca es hit, aunque el
    pX calculado a partir de ese valor de corte parezca superar el umbral."""
    if px is None or pd.isna(px):
        return False
    if relation in (">", ">="):
        return False
    return px >= HIT_PX_CUTOFF


def _morgan_fp(mol: Chem.Mol):
    return AllChem.GetMorganFingerprintAsBitVect(mol, MORGAN_RADIUS, nBits=MORGAN_NBITS)


def _load_chembl(pathogen: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (funcional, binding_real) ya con mol/inchikey/pX calculados."""
    path = settings.data_raw_dir / f"chembl_{_slug(pathogen)}.csv"
    df = pd.read_csv(path)

    df["mol"] = df["canonical_smiles"].map(_mol_from_smiles)
    n_invalid = df["mol"].isna().sum()
    df = df[df["mol"].notna()].copy()

    df["inchikey"] = df["mol"].map(MolToInchiKey)
    df["value_molar"] = [
        _to_molar(v, u, m) for v, u, m in zip(df["standard_value"], df["standard_units"], df["mol"])
    ]
    n_unit_dropped = df["value_molar"].isna().sum()
    df = df[df["value_molar"].notna()].copy()
    df["pX"] = df["value_molar"].map(_px)
    df = df[df["pX"].notna()].copy()

    print(
        f"[curate:{pathogen}] chembl: {len(df)} filas utilizables "
        f"({n_invalid} SMILES invalido, {n_unit_dropped} unidad no convertible)"
    )

    binding_mask = df["standard_type"].isin(BINDING_STANDARD_TYPES)
    binding = df[binding_mask].copy()
    functional = df[~binding_mask].copy()
    return functional, binding


def _dedup_dose_response(pathogen: str, dr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Colapsa duplicados de COADD_ID dentro de dose-response quedandose con
    el de NASSAYS mas alto. Devuelve (deduplicado, reporte_de_discrepancias).

    exceeds_tolerance solo se evalua quando todas las filas del grupo tienen
    la MISMA relacion ('=', '>', etc.) -- comparar un valor exacto ("=5.2")
    contra uno censurado (">32") del mismo COADD_ID no es una discrepancia de
    medida, son dos formatos de ensayo distintos; eso se marca aparte como
    mixed_relation, no como discrepancia."""
    reports = []
    kept_rows = []
    for coadd_id, group in dr.groupby("COADD_ID"):
        if len(group) == 1:
            kept_rows.append(group.iloc[0])
            continue

        mixed_relation = group["dr_relation"].nunique() > 1
        px_values = group["pX"].dropna()
        log_diff = float(px_values.max() - px_values.min()) if len(px_values) > 1 else 0.0
        exceeds = (not mixed_relation) and log_diff > DUPLICATE_LOG_DIFF_TOLERANCE
        best = group.loc[group["NASSAYS"].idxmax()]
        kept_rows.append(best)

        for _, row in group.iterrows():
            reports.append(
                {
                    "COADD_ID": coadd_id,
                    "DRVAL_MEDIAN": row["DRVAL_MEDIAN"],
                    "dr_relation": row["dr_relation"],
                    "DRVAL_UNIT": row["DRVAL_UNIT"],
                    "pX": row["pX"],
                    "NASSAYS": row["NASSAYS"],
                    "log_diff_within_group": log_diff,
                    "mixed_relation": mixed_relation,
                    "exceeds_tolerance": exceeds,
                    "kept": row["NASSAYS"] == best["NASSAYS"],
                }
            )

    dedup = pd.DataFrame(kept_rows).reset_index(drop=True)
    report_df = pd.DataFrame(reports)

    if not report_df.empty:
        out_path = (
            settings.data_processed_dir
            / f"discrepancies_dose_response_duplicates_{_slug(pathogen)}.csv"
        )
        report_df.to_csv(out_path, index=False)
        n_groups = report_df["COADD_ID"].nunique()
        n_mixed = report_df[report_df["mixed_relation"]]["COADD_ID"].nunique()
        n_exceeding = report_df[report_df["exceeds_tolerance"]]["COADD_ID"].nunique()
        print(
            f"[curate:{pathogen}] coadd dose-response: {n_groups} COADD_ID duplicados "
            f"({n_mixed} exacto/censurado mezclado, no es discrepancia real), "
            f"{n_exceeding} superan tolerancia de {DUPLICATE_LOG_DIFF_TOLERANCE} log -> {out_path}"
        )

    return dedup, report_df


def _load_coadd(pathogen: str) -> pd.DataFrame:
    """Left join inhibition <- dose_response sobre (COADD_ID, organismo).
    Devuelve una fila por compuesto cribado contra este patogeno."""
    slug = _slug(pathogen)
    inhib = pd.read_csv(settings.data_raw_dir / f"coadd_inhibition_{slug}.csv")
    dr = pd.read_csv(settings.data_raw_dir / f"coadd_dose_response_{slug}.csv")

    inhib["mol"] = inhib["SMILES"].map(_mol_from_smiles)
    n_invalid = inhib["mol"].isna().sum()
    inhib = inhib[inhib["mol"].notna()].copy()
    inhib["inchikey"] = inhib["mol"].map(MolToInchiKey)

    dr["mol"] = dr["SMILES"].map(_mol_from_smiles)
    dr = dr[dr["mol"].notna()].copy()

    parsed = dr["DRVAL_MEDIAN"].map(_parse_relational_value)
    dr["dr_relation"] = [r for r, _ in parsed]
    dr["dr_value"] = [v for _, v in parsed]
    n_unparsable = dr["dr_value"].isna().sum()
    dr = dr[dr["dr_value"].notna()].copy()

    dr["value_molar"] = [
        _to_molar(v, u, m) for v, u, m in zip(dr["dr_value"], dr["DRVAL_UNIT"], dr["mol"])
    ]
    dr = dr[dr["value_molar"].notna()].copy()
    dr["pX"] = dr["value_molar"].map(_px)
    dr = dr[dr["pX"].notna()].copy()
    if n_unparsable:
        print(f"[curate:{pathogen}] coadd dose-response: {n_unparsable} DRVAL_MEDIAN no parseable, descartadas")

    dr_dedup, _ = _dedup_dose_response(pathogen, dr)

    # organismo ya es constante dentro de cada fichero (coadd_loader ya filtro
    # por patogeno), asi que el join clave es solo COADD_ID -- pero se hace
    # explicitamente aqui, no se asume, para dejar constancia de que la
    # verificacion organismo=organismo ya la hizo el propio fichero de entrada.
    assert (inhib["ORGANISM"].str.lower() == pathogen.lower()).all()
    assert (dr_dedup["ORGANISM"].str.lower() == pathogen.lower()).all()

    merged = inhib.merge(
        dr_dedup[["COADD_ID", "pX", "dr_relation", "DRVAL_MEDIAN", "DRVAL_UNIT", "DRVAL_TYPE"]],
        on="COADD_ID",
        how="left",
        suffixes=("", "_dr"),
    )

    n_with_dr = merged["pX"].notna().sum()
    print(
        f"[curate:{pathogen}] coadd inhibition: {len(merged)} filas "
        f"({n_invalid} SMILES invalido descartado), {n_with_dr} con MIC "
        f"confirmado por dose-response"
    )
    return merged


def _assign_is_hit(
    df: pd.DataFrame, px_col: str, relation_col: str, inhib_col: str | None
) -> pd.Series:
    """is_hit=True solo si hay un valor de potencia real (no censurado
    'hacia arriba' por un '>'/'>=') que supera HIT_PX_CUTOFF; si no hay
    valor de potencia (solo el punto unico de CO-ADD), se usa el umbral de
    hit propio de CO-ADD sobre INHIB_AVE."""
    has_px = df[px_col].notna()
    is_hit = pd.Series(False, index=df.index)
    is_hit[has_px] = [
        _resolve_is_hit(px, rel)
        for px, rel in zip(df.loc[has_px, px_col], df.loc[has_px, relation_col])
    ]
    if inhib_col is not None:
        no_px = ~has_px
        is_hit[no_px] = df.loc[no_px, inhib_col] >= COADD_HIT_INHIB_THRESHOLD
    return is_hit


def _mark_trivial_negatives(coadd_df: pd.DataFrame, anchor_fps: list) -> pd.Series:
    candidate_mask = (
        coadd_df["pX"].isna()  # sin seguimiento en dose-response
        & (coadd_df["INHIB_AVE"] < TRIVIAL_INHIB_CEILING)
    )
    trivial = pd.Series(False, index=coadd_df.index)

    if not anchor_fps:
        # sin anclas de referencia no se puede evaluar similitud -> no se
        # marca nada como trivial (mejor no undersamplear que undersamplear mal)
        return trivial

    candidates = coadd_df[candidate_mask]
    for idx, mol in zip(candidates.index, candidates["mol"]):
        fp = _morgan_fp(mol)
        max_sim = max(DataStructs.BulkTanimotoSimilarity(fp, anchor_fps))
        if max_sim < TRIVIAL_TANIMOTO_CEILING:
            trivial.loc[idx] = True

    return trivial


def _undersample_trivial_negatives(
    pathogen: str, coadd_df: pd.DataFrame, trivial_mask: pd.Series
) -> pd.DataFrame:
    n_trivial = int(trivial_mask.sum())
    n_non_trivial = len(coadd_df) - n_trivial
    cap = n_non_trivial * TRIVIAL_UNDERSAMPLE_RATIO

    rng = random.Random(RANDOM_SEED)
    trivial_idx = list(coadd_df.index[trivial_mask])

    n_before = len(coadd_df)
    n_hits_before = int(coadd_df["is_hit"].sum())

    if n_trivial > cap:
        keep_trivial_idx = set(rng.sample(trivial_idx, cap))
        drop_idx = [i for i in trivial_idx if i not in keep_trivial_idx]
        coadd_df = coadd_df.drop(index=drop_idx)

    n_after = len(coadd_df)
    n_hits_after = int(coadd_df["is_hit"].sum())

    print(
        f"[curate:{pathogen}] undersampling triviales: {n_trivial} candidatos "
        f"(tope {cap} = {TRIVIAL_UNDERSAMPLE_RATIO}x{n_non_trivial} no-triviales) -> "
        f"filas {n_before} -> {n_after}, ratio positivos/total "
        f"{n_hits_before}/{n_before} ({n_hits_before / n_before:.4%}) -> "
        f"{n_hits_after}/{n_after} ({n_hits_after / n_after:.4%})"
    )
    return coadd_df


def _cross_source_discrepancies(
    pathogen: str, chembl_functional: pd.DataFrame, coadd_df: pd.DataFrame
) -> pd.DataFrame:
    # solo se comparan medidas reales (relation "="): comparar un valor exacto
    # contra un limite ">"/"<" no es una discrepancia, es lo esperado.
    chembl_real = chembl_functional[chembl_functional["standard_relation"] == "="]
    coadd_real = coadd_df[coadd_df["dr_relation"] == "="]

    chembl_px = chembl_real[["inchikey", "pX", "canonical_smiles"]].rename(
        columns={"pX": "pX_chembl", "canonical_smiles": "smiles_chembl"}
    )
    coadd_px = coadd_real[["inchikey", "pX", "SMILES"]].rename(
        columns={"pX": "pX_coadd", "SMILES": "smiles_coadd"}
    )

    pairs = chembl_px.merge(coadd_px, on="inchikey", how="inner")
    if pairs.empty:
        print(f"[curate:{pathogen}] solapamiento ChEMBL/CO-ADD con pX real: 0 pares")
        return pairs

    pairs["log10_diff"] = (pairs["pX_chembl"] - pairs["pX_coadd"]).abs()
    pairs["exceeds_threshold"] = pairs["log10_diff"] > CROSS_SOURCE_LOG_DIFF_THRESHOLD

    out_path = settings.data_processed_dir / f"discrepancies_chembl_coadd_{_slug(pathogen)}.csv"
    pairs.to_csv(out_path, index=False)

    n_exceeding = int(pairs["exceeds_threshold"].sum())
    print(
        f"[curate:{pathogen}] solapamiento ChEMBL/CO-ADD con pX real: {len(pairs)} pares, "
        f"{n_exceeding} ({n_exceeding / len(pairs):.1%}) superan {CROSS_SOURCE_LOG_DIFF_THRESHOLD} "
        f"log de diferencia -> {out_path}"
    )
    return pairs


def _curate_pathogen(pathogen: str) -> None:
    chembl_functional, chembl_binding = _load_chembl(pathogen)
    coadd_df = _load_coadd(pathogen)

    chembl_functional["is_hit"] = _assign_is_hit(
        chembl_functional, "pX", "standard_relation", None
    )
    coadd_df["is_hit"] = _assign_is_hit(coadd_df, "pX", "dr_relation", "INHIB_AVE")

    _cross_source_discrepancies(pathogen, chembl_functional, coadd_df)

    anchor_fps = [
        _morgan_fp(mol)
        for mol in pd.concat(
            [chembl_functional.loc[chembl_functional["is_hit"], "mol"], coadd_df.loc[coadd_df["is_hit"], "mol"]]
        )
    ]
    trivial_mask = _mark_trivial_negatives(coadd_df, anchor_fps)
    coadd_df = _undersample_trivial_negatives(pathogen, coadd_df, trivial_mask)

    chembl_out = pd.DataFrame(
        {
            "pathogen": pathogen,
            "source": "chembl",
            "compound_id": chembl_functional["molecule_chembl_id"],
            "smiles": chembl_functional["canonical_smiles"],
            "inchikey": chembl_functional["inchikey"],
            "assay_measure": chembl_functional["standard_type"],
            "raw_value": chembl_functional["standard_value"],
            "raw_unit": chembl_functional["standard_units"],
            "relation": chembl_functional["standard_relation"],
            "censored": chembl_functional["standard_relation"] != "=",
            "pX": chembl_functional["pX"],
            "is_hit": chembl_functional["is_hit"],
            "document_year": chembl_functional["document_year"],
        }
    )

    coadd_out = pd.DataFrame(
        {
            "pathogen": pathogen,
            "source": "coadd",
            "compound_id": coadd_df["COADD_ID"],
            "smiles": coadd_df["SMILES"],
            "inchikey": coadd_df["inchikey"],
            "assay_measure": coadd_df["DRVAL_TYPE"].fillna("INHIB_SINGLE_CONC"),
            "raw_value": coadd_df["DRVAL_MEDIAN"].fillna(coadd_df["INHIB_AVE"]),
            "raw_unit": coadd_df["DRVAL_UNIT"].fillna("% inhibicion @ " + coadd_df["CONC"].astype(str)),
            "relation": coadd_df["dr_relation"],
            "censored": coadd_df["pX"].isna() | (coadd_df["dr_relation"] != "="),
            "pX": coadd_df["pX"],
            "is_hit": coadd_df["is_hit"],
            "document_year": pd.NA,
        }
    )

    curated = pd.concat([chembl_out, coadd_out], ignore_index=True)
    out_path = settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv"
    curated.to_csv(out_path, index=False)
    print(f"[curate:{pathogen}] dataset curado: {len(curated)} filas -> {out_path}")

    binding_out = pd.DataFrame(
        {
            "pathogen": pathogen,
            "compound_id": chembl_binding["molecule_chembl_id"],
            "smiles": chembl_binding["canonical_smiles"],
            "inchikey": chembl_binding["inchikey"],
            "target_chembl_id": chembl_binding["target_chembl_id"],
            "assay_measure": chembl_binding["standard_type"],
            "pX": chembl_binding["pX"],
            "relation": chembl_binding["standard_relation"],
        }
    )
    binding_path = settings.data_processed_dir / f"verification_binding_{_slug(pathogen)}.csv"
    binding_out.to_csv(binding_path, index=False)
    print(f"[curate:{pathogen}] verificacion de binding real (Fase 7): {len(binding_out)} filas -> {binding_path}")


def build_curated_dataset() -> None:
    settings.data_processed_dir.mkdir(parents=True, exist_ok=True)
    for pathogen in settings.pathogens:
        _curate_pathogen(pathogen)
