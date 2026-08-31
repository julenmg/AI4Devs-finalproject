"""Fase 6 - Universo de cribado y clasificacion en cubos del caso de estudio.

Separado del agente a proposito: aqui no hay LLM. Define QUE se criba, con que
evidencia real cuenta cada candidato y en que cubo cae, todo derivado de los CSV
de Fase 1. El agente consume el resultado; no lo decide.

TERMINOLOGIA - "compuesto de coleccion clinica", nunca "farmaco aprobado".
El universo del cribado A es la libreria `NIH (USA) - Clinical Collection` de
CO-ADD: 700 compuestos que alcanzaron fase clinica, que NO es lo mismo que estar
aprobado y comercializado hoy. El dataset no trae `max_phase` de ChEMBL y no se
ha resuelto la correspondencia CO-ADD->ChEMBL para los ~585 compuestos sin ficha
(ver README, seccion 8), asi que el sistema no puede afirmar "aprobado" sin
inventarselo. Esta constante existe para que la etiqueta sea la misma en codigo,
en la salida al usuario y en la documentacion.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from app.config import settings
from app.generation.rag.corpus import INHIBITION_ONLY, _slug

CLINICAL_LIBRARY = "NIH (USA) - Clinical Collection"
CLINICAL_LABEL = "compuesto de coleccion clinica (alcanzo fase clinica; no implica aprobacion vigente)"

# Umbrales heredados de la curacion de Fase 1 (curate_dataset.py). Se reutilizan
# tal cual, sin reajustar: fijarlos ahora en funcion de como quedan los cubos
# seria elegir el resultado.
COADD_HIT_INHIB_THRESHOLD = 80.0
TRIVIAL_INHIB_CEILING = 25.0

SCREEN_CLINICAL = "A_coleccion_clinica"
SCREEN_TRANSFER = "B_transferencia_entre_patogenos"

# Cubos del cribado A (validacion retrospectiva)
BUCKET_RECOVERY = "recuperacion"
BUCKET_DISAGREEMENT = "desacuerdo_modelo_experimento"
BUCKET_NEGATIVE_AGREEMENT = "concordancia_negativa"
# Cubo del cribado B (el caso de reposicionamiento propiamente dicho)
BUCKET_HYPOTHESIS = "hipotesis_transferencia"

# Nivel de evidencia experimental disponible por candidato
EV_MIC = "mic_confirmado"      # tiene dose-response con MIC determinado
EV_SCREEN = "solo_cribado"     # solo un punto de concentracion unica
EV_NONE = "sin_medida"         # nunca se midio contra ESTE patogeno


@dataclass
class Candidate:
    pathogen: str
    screen: str
    compound_id: str
    inchikey: str
    compound_name: str
    smiles: str
    evidence_level: str
    is_hit_real: bool = False
    inhib_ave: float | None = None
    px_real: float | None = None
    in_dti_test_split: bool = False
    seen_in_training: bool = False
    source_pathogen: str = ""          # cribado B: donde SI es activo
    source_px: float | None = None
    notes: list[str] = field(default_factory=list)


def _name_lookup(pathogen: str) -> dict[str, str]:
    """compound_id -> nombre, uniendo las dos fuentes (ChEMBL y CO-ADD)."""
    slug = _slug(pathogen)
    names: dict[str, str] = {}
    chembl = pd.read_csv(
        settings.data_raw_dir / f"chembl_{slug}.csv",
        usecols=["molecule_chembl_id", "molecule_pref_name"],
    ).dropna()
    names.update(dict(chembl.drop_duplicates("molecule_chembl_id").values))
    coadd = pd.read_csv(
        settings.data_raw_dir / f"coadd_inhibition_{slug}.csv",
        usecols=["COADD_ID", "COMPOUND_NAME"],
    ).dropna()
    names.update(dict(coadd.drop_duplicates("COADD_ID").values))
    return names


def _test_inchikeys() -> dict[str, set[str]]:
    path = settings.data_processed_dir / "split_test_inchikeys.json"
    payload = json.loads(path.read_text())
    return {k: set(v) for k, v in payload.get("inchikeys", {}).items()}


def _curated(pathogen: str) -> pd.DataFrame:
    return pd.read_csv(settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv")


def clinical_collection_ids(pathogen: str) -> pd.DataFrame:
    """Los 700 compuestos de la coleccion clinica cribados contra el patogeno,
    con su porcentaje de inhibicion del cribado primario."""
    inhib = pd.read_csv(
        settings.data_raw_dir / f"coadd_inhibition_{_slug(pathogen)}.csv",
        usecols=["COADD_ID", "LIBRARY_NAME", "COMPOUND_NAME", "INHIB_AVE"],
    )
    return inhib[inhib["LIBRARY_NAME"] == CLINICAL_LIBRARY].copy()


def build_screen_a(pathogen: str) -> list[Candidate]:
    """Cribado A - validacion retrospectiva sobre la coleccion clinica.

    Los 700 se predicen enteros; NO se excluye a nadie por estar en el hold-out
    de Fase 3. Excluirlos dejaria fuera precisamente a los compuestos con
    evidencia real y sin contaminacion. Se etiquetan (`in_dti_test_split`,
    `seen_in_training`) para que la separacion sea visible en la salida y para
    que Fase 7 pueda filtrar por metadata.
    """
    clinical = clinical_collection_ids(pathogen)
    curated = _curated(pathogen)
    test_keys = _test_inchikeys().get(pathogen, set())
    inhib_by_id = dict(zip(clinical["COADD_ID"], clinical["INHIB_AVE"]))

    rows = curated[curated["compound_id"].isin(set(clinical["COADD_ID"]))]
    candidates: list[Candidate] = []

    for compound_id, group in rows.groupby("compound_id"):
        first = group.iloc[0]
        has_mic = (group["assay_measure"] != INHIBITION_ONLY).any()
        is_hit = bool(group["is_hit"].any())
        measured = group.loc[group["relation"].isin(["=", "<", "<="]), "pX"].dropna()
        in_test = str(first["inchikey"]) in test_keys

        candidates.append(
            Candidate(
                pathogen=pathogen,
                screen=SCREEN_CLINICAL,
                compound_id=str(compound_id),
                inchikey=str(first["inchikey"]),
                compound_name=str(clinical.loc[
                    clinical["COADD_ID"] == compound_id, "COMPOUND_NAME"
                ].iloc[0]),
                smiles=str(first["smiles"]),
                evidence_level=EV_MIC if has_mic else EV_SCREEN,
                is_hit_real=is_hit,
                inhib_ave=float(inhib_by_id.get(compound_id, float("nan"))),
                px_real=float(measured.max()) if not measured.empty else None,
                in_dti_test_split=in_test,
                # el LoRA v1 solo entreno con filas de potencia exacta o acotada:
                # un compuesto solo-cribado nunca lo vio, este o no en el hold-out
                seen_in_training=bool(has_mic and not in_test),
            )
        )
    return candidates


def build_screen_b(pathogen: str, only_named: bool = True) -> list[Candidate]:
    """Cribado B - transferencia entre patogenos, el caso de reposicionamiento.

    Candidato = compuesto con actividad CONFIRMADA contra el otro patogeno y
    SIN NINGUNA medida contra este. La pertenencia la decide un hecho del dato
    (ausencia total de medida), no un umbral: por eso este cubo no se solapa con
    el de desacuerdo, donde si hay medida y dice inactivo.

    Se restringe por defecto a compuestos con nombre conocido: el entregable es
    una lista corta que un humano pueda leer y priorizar.
    """
    other = next(p for p in settings.pathogens if p != pathogen)
    here, there = _curated(pathogen), _curated(other)
    names = _name_lookup(other)
    test_keys = _test_inchikeys().get(other, set())

    sin_medida = set(there.loc[there["is_hit"], "inchikey"]) - set(here["inchikey"])
    source = there[there["inchikey"].isin(sin_medida)]

    candidates: list[Candidate] = []
    for inchikey, group in source.groupby("inchikey"):
        name = next(
            (names[c] for c in group["compound_id"] if c in names and pd.notna(names[c])), ""
        )
        if only_named and not name:
            continue
        best = group.loc[group["relation"].isin(["=", "<", "<="]), "pX"].dropna()
        candidates.append(
            Candidate(
                pathogen=pathogen,
                screen=SCREEN_TRANSFER,
                compound_id=str(group.iloc[0]["compound_id"]),
                inchikey=str(inchikey),
                compound_name=str(name),
                smiles=str(group.iloc[0]["smiles"]),
                evidence_level=EV_NONE,
                is_hit_real=False,
                inhib_ave=None,
                px_real=None,
                in_dti_test_split=str(inchikey) in test_keys,
                seen_in_training=False,  # nunca se midio contra ESTE patogeno
                source_pathogen=other,
                source_px=float(best.max()) if not best.empty else None,
            )
        )
    return candidates


def assign_bucket(row: pd.Series, pred_threshold: float) -> str:
    """Clasifica un candidato ya predicho.

    Cribado B: todo va a `hipotesis_transferencia` — hay evidencia real de
    actividad en el otro patogeno y ninguna medida en este.

    Cribado A: el cubo depende de la evidencia experimental disponible, y solo
    el desempate lo pone la prediccion:
      - `recuperacion`         activo confirmado por MIC real (el modelo deberia
                               ordenarlo alto; es la validacion del pipeline).
      - `desacuerdo`           el modelo predice por encima del umbral pero la
                               medida real dice que no hay actividad.
      - `concordancia_negativa` el modelo tambien lo pone bajo. Es la mayoria, y
                               enseña que el modelo no puntua alto a todo.
    """
    if row["screen"] == SCREEN_TRANSFER:
        return BUCKET_HYPOTHESIS
    if row["is_hit_real"]:
        return BUCKET_RECOVERY
    if row["pred_pmic"] >= pred_threshold:
        return BUCKET_DISAGREEMENT
    return BUCKET_NEGATIVE_AGREEMENT
