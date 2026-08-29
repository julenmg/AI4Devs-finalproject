"""Fase 5 - Construye el corpus de evidencia real que se indexa en el RAG.

Convierte los CSV de Fases 1 en documentos de texto citables. Ningun texto de
este modulo se redacta a mano libre: todo sale de una plantilla determinista
alimentada con filas reales de ChEMBL / CO-ADD, de modo que cualquier cifra que
el LLM llegue a citar existe en un fichero del repo y es verificable.

Cinco clases de evidencia (`evidence_class`):

  phenotypic_potency      ficha por (compuesto x patogeno) con su potencia
                          medida in vitro. El nucleo del corpus.
  primary_screen_summary  agregados del cribado primario de CO-ADD por
                          libreria. Resumen las ~173k filas inhibition-only
                          que no merecen una ficha individual (serian chunks
                          casi identicos diciendo "sin actividad") pero que
                          son evidencia real de INACTIVIDAD y no deben
                          desaparecer en silencio.
  binding_specific        las 66 filas Ki/Kd reales contra una diana
                          molecular concreta. Unica evidencia de AFINIDAD DE
                          UNION de todo el proyecto; el resto es potencia
                          fenotipica. Marcadas holdout_fase7=True.
  background              fichas de patogeno (mismo material que el contexto
                          fijo del CAG de Fase 4), para que el RAG cubra al
                          menos lo que cubria el CAG.
  methodology             como se construyo el dataset, con las cifras reales
                          de la curacion. Permite responder "de donde sale
                          este dato" sin que el modelo se lo invente.

Por que se releen los CSV de data/raw ademas del curado: la curacion de Fase 1
descarto los campos que no hacian falta para entrenar pero que son justo los
que permiten CITAR (molecule_pref_name, assay_chembl_id, STRAIN, LIBRARY_NAME,
CONC). Aqui se vuelven a unir por compound_id. No se re-curan datos: el valor,
la relacion y el is_hit salen siempre del CSV curado.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.config import settings

# ---------------------------------------------------------------------------
# Constantes de encuadre. HIT_PX_CUTOFF replica el umbral de curate_dataset.py
# (no se recalcula is_hit aqui, solo se explica en el texto de la ficha).
HIT_PX_CUTOFF = 5.0
INHIBITION_ONLY = "INHIB_SINGLE_CONC"

SOURCE_URLS = {
    "chembl": "https://www.ebi.ac.uk/chembl/",
    "coadd": "https://db.co-add.org/",
}

# Frase fija que acompana a toda evidencia de potencia. Se repite a proposito en
# cada ficha: si un chunk se recupera aislado, la frontera viaja con el.
FRONTIER_NOTE = (
    "Medida de potencia fenotipica in vitro (concentracion que inhibe el "
    "crecimiento del cultivo). No es afinidad de union a una diana concreta "
    "ni prediccion de eficacia clinica."
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


@dataclass
class EvidenceDoc:
    """Un documento indexable. `metadata` solo admite escalares (str/int/float/
    bool): es el tipo que acepta el `where` de Chroma para el prefiltrado.

    `search_text` es lo que se EMBEBE; `text` es lo que se muestra y se cita. Se
    separan porque las fichas comparten mucho texto de plantilla (la nota de
    frontera, los encabezados, el SMILES) y al embeber la ficha entera ese texto
    comun domina el vector: medido, dos fichas de compuestos DISTINTOS salian a
    0.94 de similitud, mas cerca entre si que la consulta de su propia ficha
    (0.90). Con 21 000 fichas eso hunde el retrieval. El search_text deja solo
    lo que distingue una ficha de otra.
    """

    doc_id: str
    text: str
    metadata: dict = field(default_factory=dict)
    search_text: str | None = None

    def embed_text(self) -> str:
        return self.search_text or self.text


# ---------------------------------------------------------------------------
# Utilidades de formato


# Toda comprobacion de "hay valor?" pasa por aqui. Motivo: bool(float("nan"))
# es True, asi que un `if valor:` deja pasar un NaN de pandas y acaba escrito en
# la ficha como "nan" (paso con COMPOUND_NAME: 8 295 fichas decian "Compuesto:
# Nan", y lo detecto el modelo leyendo la evidencia, no una revision de codigo).
# Se cubre tambien la cadena "nan" porque algunos campos llegan ya convertidos a
# str aguas arriba (curate_dataset compone raw_unit con .astype(str), que
# convierte un NaN en el literal "nan").
_MISSING_STRINGS = {"", "nan", "none", "null", "nat", "<na>"}


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass  # listas, dicts y demas no escalares: no son "missing"
    return isinstance(value, str) and value.strip().lower() in _MISSING_STRINGS


def _text(value, default: str = "") -> str:
    """Valor listo para interpolar en una plantilla, o el default si falta."""
    return default if _is_missing(value) else str(value).strip()


def _fmt(value: float, digits: int = 2) -> str:
    if _is_missing(value):
        return "?"
    if abs(value) >= 1000 or (abs(value) < 0.01 and value != 0):
        return f"{value:.3g}"
    return f"{round(float(value), digits):g}"


def _fmt_range(lo: float, hi: float, digits: int = 2) -> str:
    if lo == hi:
        return _fmt(lo, digits)
    return f"{_fmt(lo, digits)}-{_fmt(hi, digits)}"


def _join_ids(values, limit: int = 5) -> str:
    uniq = sorted({str(v).strip() for v in values if not _is_missing(v)})
    if not uniq:
        return ""
    if len(uniq) <= limit:
        return ", ".join(uniq)
    return ", ".join(uniq[:limit]) + f" (+{len(uniq) - limit} mas)"


# ---------------------------------------------------------------------------
# Carga de las fuentes


def _load_raw_chembl(pathogen: str) -> pd.DataFrame:
    path = settings.data_raw_dir / f"chembl_{_slug(pathogen)}.csv"
    return pd.read_csv(path)


def _chembl_compound_meta(pathogen: str) -> dict:
    """Por molecule_chembl_id: nombre preferido, ensayos y dianas citables.
    Se devuelve como dict: se consulta una vez por ficha (~34k veces) y un
    .loc de pandas por consulta era el segundo cuello de botella."""
    raw = _load_raw_chembl(pathogen)
    grouped = raw.groupby("molecule_chembl_id").agg(
        compound_name=(
            "molecule_pref_name",
            lambda s: next((v for v in s if not _is_missing(v)), ""),
        ),
        assay_ids=("assay_chembl_id", lambda s: _join_ids(s, limit=3)),
        target_ids=("target_chembl_id", lambda s: _join_ids(s, limit=3)),
        year_min=("document_year", "min"),
        year_max=("document_year", "max"),
    )
    return grouped.to_dict("index")


def _coadd_compound_meta(pathogen: str) -> pd.DataFrame:
    """Por COADD_ID: nombre, libreria, cepa y concentracion del cribado."""
    slug = _slug(pathogen)
    inhib = pd.read_csv(
        settings.data_raw_dir / f"coadd_inhibition_{slug}.csv",
        usecols=[
            "COADD_ID",
            "COMPOUND_NAME",
            "LIBRARY_NAME",
            "STRAIN",
            "CONC",
            "ASSAY_ID",
            "INHIB_AVE",
        ],
    )
    inhib = inhib.set_index("COADD_ID")

    dr = pd.read_csv(
        settings.data_raw_dir / f"coadd_dose_response_{slug}.csv",
        usecols=["COADD_ID", "STRAIN", "DRVAL_TYPE", "ASSAY_ID"],
    )
    # una fila por COADD_ID: las cepas del dose-response se concatenan porque
    # el mismo compuesto puede confirmarse contra varias (p.ej. ATCC 13883 y
    # la NDM-1 BAA-2146 en K. pneumoniae) y ambas son citables.
    dr_grouped = dr.groupby("COADD_ID").agg(
        dr_strains=("STRAIN", lambda s: _join_ids(s, limit=3)),
        dr_assay_ids=("ASSAY_ID", lambda s: _join_ids(s, limit=3)),
    )
    return inhib.join(dr_grouped, how="left")


def _load_test_inchikeys() -> dict[str, set[str]]:
    """Hold-out del fine-tune de Fase 3. Se propaga a la metadata de cada ficha
    (`in_dti_test_split`) para que la evaluacion de Fase 7 pueda excluir del
    retrieval los compuestos cuyo MIC real el agente no deberia poder leer
    antes de que el DTI lo prediga. Sin esta marca, el agente de Fase 6 podria
    'acertar' copiando la respuesta del contexto recuperado."""
    path = settings.data_processed_dir / "split_test_inchikeys.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    return {k: set(v) for k, v in payload.get("inchikeys", {}).items()}


# ---------------------------------------------------------------------------
# Fichas de compuesto (evidence_class = phenotypic_potency)


def _relation_class(relation, assay_measure: str) -> str:
    if assay_measure == INHIBITION_ONLY:
        return "single_conc"
    if relation in (">", ">="):
        return "upper_censored"
    if relation in ("<", "<="):
        return "lower_censored"
    if relation == "=" or relation == "~":
        return "exact"
    return "unknown"


def _source_label(source: str, assay_measure: str) -> str:
    if source == "chembl":
        return "ChEMBL"
    if assay_measure == INHIBITION_ONLY:
        return "CO-ADD cribado primario"
    return "CO-ADD dose-response"


def _year_span(years) -> str:
    valid = [int(y) for y in years if not _is_missing(y)]
    if not valid:
        return ""
    lo, hi = min(valid), max(valid)
    return f", {lo}" if lo == hi else f", {lo}-{hi}"


def _num(value) -> float | None:
    """raw_value llega mezclado: numerico en ChEMBL, string con el operador
    embebido en CO-ADD (p.ej. '>32', porque DRVAL_MEDIAN lo trae asi)."""
    if _is_missing(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _RELATIONAL_VALUE_RE.match(str(value).strip())
    return float(match.group(2)) if match else None


_RELATIONAL_VALUE_RE = re.compile(r"^(<=|>=|<|>|~)?\s*([-+]?[0-9]*\.?[0-9]+)$")


def _potency_bullets(rows: list[dict], coadd_meta: dict) -> list[str]:
    """Un bullet por (fuente, medida, tipo de relacion, unidad).

    El texto de cada tipo de relacion es fijo: una fila '>' se redacta SIEMPRE
    como cota ('no se observo inhibicion hasta X'), nunca como 'inactivo',
    porque el ensayo no probo concentraciones mas altas y afirmar inactividad
    seria ir mas lejos que el dato.

    Trabaja sobre listas de dicts, no sobre DataFrames: son ~34k fichas y un
    groupby de pandas por ficha dominaba el tiempo de construccion del corpus.
    """
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row["source"],
            row["assay_measure"],
            _relation_class(row["relation"], row["assay_measure"]),
            row["raw_unit"],
        )
        buckets.setdefault(key, []).append(row)

    order = {"exact": 0, "lower_censored": 1, "upper_censored": 2, "single_conc": 3, "unknown": 4}
    bullets: list[str] = []

    for (source, measure, rel_class, unit), grp in sorted(
        buckets.items(), key=lambda kv: (order.get(kv[0][2], 9), -len(kv[1]))
    ):
        label = _source_label(source, measure)
        unit = _text(unit, "unidad no indicada")
        n = len(grp)
        span = _year_span([r.get("document_year") for r in grp])
        px = [r["pX"] for r in grp if not _is_missing(r["pX"])]
        px_txt = ""
        if px:
            px_txt = f" -> p{measure} {_fmt_range(min(px), max(px))}"
            if len(px) > 2:
                px_txt += f" (mediana {_fmt(float(np.median(px)))})"

        if rel_class == "single_conc":
            strain = ""
            meta = next(
                (coadd_meta[r["compound_id"]] for r in grp if r["compound_id"] in coadd_meta),
                None,
            )
            strain_name = _text(meta.get("STRAIN")) if meta else ""
            if strain_name:
                strain = f" frente a la cepa {strain_name}"
            values = ", ".join(
                _fmt(v, 1) for v in filter(None, (_num(r["raw_value"]) for r in grp[:3]))
            )
            bullets.append(
                f"- Cribado primario CO-ADD{strain} ({unit}): {values}% de inhibicion "
                f"[{n} registro(s)]."
            )
            continue

        vals = [v for v in (_num(r["raw_value"]) for r in grp) if v is not None]
        rng = _fmt_range(min(vals), max(vals)) if vals else "?"

        if rel_class == "exact":
            bullets.append(
                f"- {measure} medida ({label}, {n} registro(s){span}): {rng} {unit}{px_txt}."
            )
        elif rel_class == "upper_censored":
            bullets.append(
                f"- {measure} acotada por arriba ({label}, {n} registro(s){span}): no se "
                f"observo inhibicion hasta {rng} {unit}, la concentracion mas alta "
                f"ensayada. El compuesto podria ser activo por encima de esa dosis: el "
                f"ensayo no lo determina."
            )
        elif rel_class == "lower_censored":
            bullets.append(
                f"- {measure} acotada por abajo ({label}, {n} registro(s){span}): activo ya "
                f"a la concentracion mas baja ensayada ({rng} {unit}), el valor real es "
                f"menor o igual{px_txt}."
            )
        else:
            bullets.append(f"- {measure} ({label}, {n} registro(s){span}): {rng} {unit}{px_txt}.")

    return bullets


def _compound_card(
    pathogen: str,
    inchikey: str,
    rows: list[dict],
    chembl_meta: dict,
    coadd_meta: dict,
    test_keys: set[str],
) -> EvidenceDoc:
    name = ""
    assay_ids, target_ids = "", ""
    strains: set[str] = set()

    for row in rows:
        cid = row["compound_id"]
        if row["source"] == "chembl" and cid in chembl_meta:
            meta = chembl_meta[cid]
            name = name or _text(meta["compound_name"])
            assay_ids = assay_ids or _text(meta["assay_ids"])
            target_ids = target_ids or _text(meta["target_ids"])
        elif row["source"] == "coadd" and cid in coadd_meta:
            meta = coadd_meta[cid]
            name = name or _text(meta.get("COMPOUND_NAME"))
            for field_name in ("STRAIN", "dr_strains"):
                val = _text(meta.get(field_name))
                if val:
                    strains.add(val)

    smiles = _text(rows[0]["smiles"], "(no disponible)")
    is_hit = any(r["is_hit"] for r in rows)
    has_exact = any(r["relation"] == "=" for r in rows)
    measured = [
        r["pX"] for r in rows if r["relation"] in ("=", "<", "<=") and not _is_missing(r["pX"])
    ]
    measure_label = _text(rows[0]["assay_measure"], "X")

    ids_txt = _join_ids([r["compound_id"] for r in rows], limit=4)
    display_name = name.title() if name else "(sin nombre asignado en las fuentes)"

    header = [
        f"Compuesto: {display_name}",
        f"Patogeno ensayado: {pathogen}",
        f"Identificadores: {ids_txt} | InChIKey: {inchikey}",
        f"SMILES: {smiles}",
    ]
    if strains:
        header.append(f"Cepas ensayadas: {'; '.join(sorted(strains))}")

    body = ["", "Evidencia experimental de potencia:"]
    body += _potency_bullets(rows, coadd_meta)

    if is_hit:
        verdict = (
            f"Clasificacion en el dataset curado: HIT (potencia medida no censurada con "
            f"p{measure_label} >= {HIT_PX_CUTOFF}, equivalente a ~10 uM o mas potente)."
        )
    elif not measured:
        verdict = (
            "Clasificacion en el dataset curado: NO HIT. Aviso: no hay ninguna medida de "
            "potencia sin censurar para este compuesto, asi que 'no hit' significa "
            "'no se demostro actividad en las condiciones ensayadas', no 'inactivo'."
        )
    else:
        verdict = (
            f"Clasificacion en el dataset curado: NO HIT (mejor potencia medida "
            f"p{measure_label} = {_fmt(max(measured))}, por debajo del umbral "
            f"{HIT_PX_CUTOFF})."
        )

    sources = sorted({r["source"] for r in rows})
    citation_bits = []
    if "chembl" in sources:
        chembl_ids = _join_ids([r["compound_id"] for r in rows if r["source"] == "chembl"], limit=2)
        cite = f"ChEMBL {chembl_ids}"
        if assay_ids:
            cite += f" (ensayos {assay_ids})"
        citation_bits.append(cite)
    if "coadd" in sources:
        coadd_ids = _join_ids([r["compound_id"] for r in rows if r["source"] == "coadd"], limit=2)
        citation_bits.append(f"CO-ADD {coadd_ids}")
    citation = " + ".join(citation_bits) + f" | {pathogen}"

    text = "\n".join(header + body + ["", verdict, "", FRONTIER_NOTE])

    metadata = {
        "evidence_class": "phenotypic_potency",
        "source": "+".join(sources),
        "pathogen": pathogen,
        "compound_name": display_name if name else "",
        "inchikey": inchikey,
        "compound_ids": ids_txt,
        "assay_measures": _join_ids([r["assay_measure"] for r in rows], limit=4),
        "is_hit": is_hit,
        "censored_only": not has_exact,
        "n_records": len(rows),
        "strains": "; ".join(sorted(strains)),
        "citation": citation,
        "source_url": SOURCE_URLS["chembl"] if "chembl" in sources else SOURCE_URLS["coadd"],
        "in_dti_test_split": inchikey in test_keys,
        "holdout_fase7": False,
    }
    if measured:
        metadata["best_pX"] = round(float(max(measured)), 4)
    years = [int(r["document_year"]) for r in rows if not _is_missing(r.get("document_year"))]
    if years:
        metadata["year_min"] = min(years)
        metadata["year_max"] = max(years)

    # Texto de busqueda: nombre primero, sin plantilla y sin SMILES. El SMILES es
    # ruido para el retrieval semantico (cadenas de simbolos casi identicas entre
    # compuestos parecidos) y ocupaba la mitad de los tokens de la ficha.
    search_bits = [display_name if name else "compuesto sin nombre", pathogen, ids_txt]
    if strains:
        search_bits.append("cepas " + "; ".join(sorted(strains)))
    search_bits.append(
        _join_ids([r["assay_measure"] for r in rows], limit=4).replace(
            INHIBITION_ONLY, "inhibicion a concentracion unica"
        )
    )
    search_bits.append("hit activo potente" if is_hit else "no hit sin actividad demostrada")
    if measured:
        search_bits.append(f"p{measure_label} maximo {_fmt(max(measured))}")
    search_text = ". ".join(b for b in search_bits if b) + "."

    return EvidenceDoc(
        doc_id=f"potency:{_slug(pathogen)}:{inchikey}",
        text=text,
        metadata=metadata,
        search_text=search_text,
    )


# ---------------------------------------------------------------------------
# Agregados de cribado primario (evidence_class = primary_screen_summary)


def _screen_summary_docs(
    pathogen: str, leftover: pd.DataFrame, coadd_meta: pd.DataFrame
) -> list[EvidenceDoc]:
    """Resume las filas inhibition-only que no reciben ficha individual.

    Son ~173k compuestos cribados a una sola concentracion y sin seguimiento:
    una ficha por compuesto serian chunks casi identicos que ahogarian el
    retrieval. Pero son evidencia real de que ESE compuesto se probo y no dio
    senal, asi que se agregan por libreria de origen en vez de descartarse.
    """
    if leftover.empty:
        return []

    joined = leftover.join(
        coadd_meta[["LIBRARY_NAME", "STRAIN", "CONC", "INHIB_AVE"]], on="compound_id"
    )
    docs: list[EvidenceDoc] = []

    # Agregado GLOBAL del patogeno, ademas del de cada libreria. Sin el, una
    # pregunta del tipo "cuantos compuestos se cribaron" solo puede responderse
    # con las librerias que quepan en el top-k (5 de 25 con k=8), y la respuesta
    # sale correcta pero parcial. Observado en la bateria de validacion: el
    # modelo sumo 1 973 compuestos de cinco librerias en vez de los 96 069
    # reales, acotando bien su afirmacion pero sin poder dar la cifra global.
    all_inhib = joined["INHIB_AVE"].dropna()
    n_total = len(joined)
    n_libraries = int(joined["LIBRARY_NAME"].nunique(dropna=True))
    global_strain = _text(
        joined["STRAIN"].dropna().iloc[0] if joined["STRAIN"].notna().any() else None, "?"
    )
    n_ge80_all = int((all_inhib >= 80).sum())
    docs.append(
        EvidenceDoc(
            doc_id=f"screen:{_slug(pathogen)}:global",
            text="\n".join(
                [
                    f"Resumen GLOBAL de cribado primario CO-ADD - {pathogen}",
                    f"Cepa ensayada: {global_strain} | Librerias distintas: {n_libraries}",
                    "",
                    f"- Total de compuestos cribados contra {pathogen} a concentracion "
                    f"unica y sin seguimiento en dose-response: {n_total}.",
                    f"- Con inhibicion >= 80% (umbral de hit de CO-ADD): {n_ge80_all}.",
                    f"- Con inhibicion >= 50%: {int((all_inhib >= 50).sum())}.",
                    f"- Con inhibicion < 25% (sin senal apreciable): "
                    f"{int((all_inhib < 25).sum())}.",
                    f"- Inhibicion mediana del conjunto: "
                    f"{_fmt(all_inhib.median(), 1) if not all_inhib.empty else '?'}%.",
                    "",
                    "Esta es la cifra agregada de TODO el cribado primario de este "
                    "patogeno. Los resumenes por libreria desglosan este mismo total.",
                    "",
                    "Porcentaje de inhibicion a un solo punto de concentracion: no es "
                    "potencia (no hay curva dosis-respuesta ni MIC) y no es prediccion "
                    "de eficacia clinica.",
                ]
            ),
            metadata={
                "evidence_class": "primary_screen_summary",
                "source": "coadd",
                "pathogen": pathogen,
                "library": f"TODAS ({n_libraries} librerias)",
                "strains": str(global_strain),
                "n_records": n_total,
                "n_hits_80": n_ge80_all,
                "citation": (
                    f"CO-ADD cribado primario | agregado global de {n_libraries} librerias "
                    f"| {pathogen} {global_strain}"
                ),
                "source_url": SOURCE_URLS["coadd"],
                "in_dti_test_split": False,
                "holdout_fase7": False,
            },
            search_text=(
                f"Total de compuestos cribados frente a {pathogen}. Cuantos compuestos "
                f"se ensayaron en total, cuantos hits, proporcion de actividad. Agregado "
                f"global de todo el cribado primario CO-ADD, {n_libraries} librerias, "
                f"cepa {global_strain}."
            ),
        )
    )

    for library, grp in joined.groupby("LIBRARY_NAME", dropna=False):
        lib_name = _text(library, "(libreria no indicada)")
        inhib = grp["INHIB_AVE"].dropna()
        strain = _text(grp["STRAIN"].dropna().iloc[0] if grp["STRAIN"].notna().any() else None, "?")
        concs = _join_ids(grp["CONC"], limit=3) or "no indicada"
        n = len(grp)
        n_ge80 = int((inhib >= 80).sum())
        n_ge50 = int((inhib >= 50).sum())
        n_lt25 = int((inhib < 25).sum())

        text = "\n".join(
            [
                f"Resumen de cribado primario CO-ADD - {pathogen}",
                f"Libreria de compuestos: {lib_name}",
                f"Cepa ensayada: {strain} | Concentracion(es): {concs}",
                "",
                f"- Compuestos cribados de esta libreria sin seguimiento en dose-response: {n}.",
                f"- Con inhibicion >= 80% (umbral de hit de CO-ADD): {n_ge80} "
                f"({n_ge80 / n:.2%}).",
                f"- Con inhibicion >= 50%: {n_ge50} ({n_ge50 / n:.2%}).",
                f"- Con inhibicion < 25% (sin senal apreciable): {n_lt25} ({n_lt25 / n:.2%}).",
                f"- Inhibicion mediana: {_fmt(inhib.median(), 1) if not inhib.empty else '?'}%, "
                f"maxima: {_fmt(inhib.max(), 1) if not inhib.empty else '?'}%.",
                "",
                "Un porcentaje de hits muy bajo es lo esperado en cribado primario: la "
                "mayor parte de una quimioteca no tiene actividad antibacteriana. Esta es "
                "la razon por la que CO-ADD aporta negativos reales, a diferencia de las "
                "bases de bioactividad que solo publican positivos.",
                "",
                "Un solo punto de concentracion no permite derivar una curva dosis-"
                "respuesta ni un MIC: estos numeros son porcentaje de inhibicion, no "
                "potencia. Tampoco son prediccion de eficacia clinica.",
            ]
        )

        docs.append(
            EvidenceDoc(
                doc_id=f"screen:{_slug(pathogen)}:{_slug(lib_name)}",
                text=text,
                metadata={
                    "evidence_class": "primary_screen_summary",
                    "source": "coadd",
                    "pathogen": pathogen,
                    "library": lib_name,
                    "strains": str(strain),
                    "n_records": n,
                    "n_hits_80": n_ge80,
                    "citation": f"CO-ADD cribado primario | {lib_name} | {pathogen} {strain}",
                    "source_url": SOURCE_URLS["coadd"],
                    "in_dti_test_split": False,
                    "holdout_fase7": False,
                },
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Binding especifico (evidence_class = binding_specific)


def _binding_docs(pathogen: str, test_keys: set[str]) -> list[EvidenceDoc]:
    """Las 66 filas Ki/Kd contra una diana molecular concreta.

    Es la unica evidencia de AFINIDAD DE UNION real del proyecto; todo lo demas
    es potencia fenotipica. Las dianas resultan ser precisamente las
    carbapenemasas y beta-lactamasas de las fichas de patogeno (KPC, OXA-48,
    NDM/metallo-beta-lactamasa, SHV, OXA-23, ADC), lo que conecta la evidencia
    molecular con el mecanismo de resistencia descrito.

    ADVERTENCIA (Fase 6/7): estas filas estan apartadas como verificacion. Van
    marcadas holdout_fase7=True para que la evaluacion pueda excluirlas del
    retrieval. Ver docs/decisions.md, Fase 5.
    """
    path = settings.data_processed_dir / f"verification_binding_{_slug(pathogen)}.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path)

    targets_path = settings.data_raw_dir / "chembl_targets.json"
    targets = json.loads(targets_path.read_text()) if targets_path.exists() else {}

    docs = []
    for idx, row in df.iterrows():
        tid = row["target_chembl_id"]
        tinfo = targets.get(str(tid), {})
        tname = _text(tinfo.get("pref_name")) or _text(tid, "(diana no identificada)")
        measure = row["assay_measure"]
        rel = row["relation"]
        px = float(row["pX"])
        rel_txt = "" if rel == "=" else f" (valor acotado, relacion '{rel}')"

        text = "\n".join(
            [
                f"Afinidad de union medida - {pathogen}",
                f"Compuesto: {row['compound_id']} | InChIKey: {row['inchikey']}",
                f"SMILES: {row['smiles']}",
                f"Diana molecular: {tname} ({tid})",
                "",
                f"- {measure} reportado en ChEMBL: p{measure} = {_fmt(px)}{rel_txt}.",
                "",
                "A diferencia del resto del corpus, esta SI es una medida de afinidad de "
                "union del compuesto a una proteina diana concreta, no potencia sobre el "
                "cultivo completo. Sigue sin ser una prediccion de eficacia clinica.",
                "",
                "Nota de procedencia: fila apartada del entrenamiento del modelo DTI "
                "(conjunto de verificacion de Fase 7).",
            ]
        )

        docs.append(
            EvidenceDoc(
                doc_id=f"binding:{_slug(pathogen)}:{row['compound_id']}:{tid}:{idx}",
                text=text,
                metadata={
                    "evidence_class": "binding_specific",
                    "source": "chembl",
                    "pathogen": pathogen,
                    "compound_name": "",
                    "inchikey": str(row["inchikey"]),
                    "compound_ids": str(row["compound_id"]),
                    "target_chembl_id": str(tid),
                    "target_name": tname,
                    "assay_measures": str(measure),
                    "best_pX": round(px, 4),
                    "is_hit": False,
                    "n_records": 1,
                    "citation": f"ChEMBL {row['compound_id']} · {measure} vs {tname} ({tid})",
                    "source_url": SOURCE_URLS["chembl"],
                    "in_dti_test_split": str(row["inchikey"]) in test_keys,
                    "holdout_fase7": True,
                },
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Fichas de patogeno (evidence_class = background)


def _background_docs() -> list[EvidenceDoc]:
    """Trocea el contexto fijo del CAG (Fase 4) por secciones '## '.

    Se reutiliza literalmente el mismo texto a proposito: garantiza que el RAG
    cubre al menos todo lo que cubria el CAG, de modo que la comparacion entre
    ambas fases mida lo que aporta el retrieval y no una diferencia de material
    de partida.
    """
    from app.generation.cag.static_context import STATIC_CONTEXT

    docs = []
    sections = re.split(r"\n(?=## )", STATIC_CONTEXT)
    for section in sections:
        section = section.strip()
        if not section.startswith("## "):
            continue
        title = section.splitlines()[0].removeprefix("## ").strip()
        if "frontera" in title.lower():
            # La seccion de frontera del CAG afirma "en esta fase CAG NO se ha
            # invocado el modelo DTI", cierto en Fase 4 y falso en cuanto el
            # agente de Fase 6 lo invoque. Se sustituye por metodo:frontera,
            # que dice lo mismo sin atarse a una fase concreta.
            continue
        pathogen = next((p for p in settings.pathogens if p.lower() in title.lower()), "")
        docs.append(
            EvidenceDoc(
                doc_id=f"ficha:{_slug(title)}",
                text=(
                    f"Ficha de contexto - {title}\n\n{section}\n\n"
                    "Procedencia: sintesis del proyecto a partir de la WHO Bacterial "
                    "Priority Pathogens List 2024 y de literatura de revision sobre "
                    "mecanismos de resistencia. No contiene valores numericos de "
                    "compuestos: para eso estan las fichas de evidencia experimental."
                ),
                metadata={
                    "evidence_class": "background",
                    "source": "cag_context",
                    "pathogen": pathogen,
                    "citation": f"Ficha de contexto EskapeGuard · {title}",
                    "source_url": "https://www.who.int/publications/i/item/9789240093461",
                    "n_records": 0,
                    "in_dti_test_split": False,
                    "holdout_fase7": False,
                },
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Metodologia (evidence_class = methodology)


def _methodology_docs(curated: dict[str, pd.DataFrame]) -> list[EvidenceDoc]:
    """Como se construyo el dataset, con las cifras calculadas en el momento a
    partir de los CSV reales (no copiadas a mano: si el dataset cambia, la
    ficha cambia con el)."""
    docs = []

    lines = [
        "Metodologia - composicion del dataset curado de EskapeGuard",
        "",
        "Fuentes: ChEMBL (API REST publica /chembl/api/data/activity) y CO-ADD "
        "(Community for Open Antimicrobial Drug Discovery, release r03 02-2020: "
        "ficheros de Inhibition y Dose Response).",
        "",
    ]
    for pathogen, df in curated.items():
        n = len(df)
        n_exact = int((df["relation"] == "=").sum())
        n_hits = int(df["is_hit"].sum())
        by_source = df.groupby(["source", "assay_measure"]).size()
        breakdown = ", ".join(f"{s}/{m}: {v}" for (s, m), v in by_source.items())
        lines += [
            f"## {pathogen}",
            f"- Filas curadas: {n}; compuestos unicos (InChIKey): {df['inchikey'].nunique()}.",
            f"- Desglose por fuente y medida: {breakdown}.",
            f"- Con valor exacto (relacion '='): {n_exact} ({n_exact / n:.2%}); el resto "
            f"es censurado o cribado a concentracion unica.",
            f"- Etiquetadas como hit: {n_hits} ({n_hits / n:.2%}).",
            "",
        ]
    lines += [
        "Reglas de curacion aplicadas:",
        f"- Umbral de hit: pX >= {HIT_PX_CUTOFF} (~10 uM o mas potente) cuando hay "
        "potencia medida; inhibicion >= 80% (umbral propio de CO-ADD) cuando solo hay "
        "cribado a concentracion unica.",
        "- Una fila con relacion '>' o '>=' NUNCA cuenta como hit: significa que no se "
        "observo efecto hasta esa dosis, no que el compuesto sea potente.",
        "- Conversion a molar dependiente del peso molecular para unidades de masa/volumen "
        "(ug/mL), directa para unidades molares. Unidades no convertibles (ppm, etc.) "
        "descartadas.",
        "- Duplicados de CO-ADD dose-response resueltos quedandose con la fila de mas "
        "ensayos (NASSAYS); discrepancias reportadas en data/processed/discrepancies_*.csv.",
        "- Los valores Ki/Kd contra dianas moleculares concretas se apartaron del dataset "
        "principal (verification_binding_*.csv) para no mezclar afinidad de union con "
        "potencia fenotipica.",
    ]

    docs.append(
        EvidenceDoc(
            doc_id="metodo:dataset",
            text="\n".join(lines),
            metadata={
                "evidence_class": "methodology",
                "source": "eskapeguard",
                "pathogen": "",
                "citation": "EskapeGuard · curacion del dataset (app/ingestion/curate_dataset.py)",
                "source_url": "https://github.com/julenmg/AI4Devs-finalproject",
                "n_records": 0,
                "in_dti_test_split": False,
                "holdout_fase7": False,
            },
        )
    )

    docs.append(
        EvidenceDoc(
            doc_id="metodo:frontera",
            text="\n".join(
                [
                    "Metodologia - que puede y que no puede afirmar EskapeGuard",
                    "",
                    "El modelo DTI del proyecto (checkpoint ibm-research/biomed.omics."
                    "bl.sm.ma-ted-458m.dti_bindingdb_pkd, ajustado con LoRA) predice "
                    "potencia fenotipica (pMIC) a partir del SMILES del compuesto.",
                    "",
                    "- El checkpoint exige una secuencia de proteina en la entrada. Se usa "
                    "la GyrA real de cada patogeno (UniProt A0A0H3H0Y6 para K. pneumoniae, "
                    "A0A0D5YFF2 para A. baumannii) como ancla de organismo. Es un REQUISITO "
                    "DE ARQUITECTURA para rellenar ese hueco, NO una afirmacion de que la "
                    "prediccion sea afinidad por la girasa ni especifica de fluoroquinolonas.",
                    "- El error tipico del modelo sobre el hold-out es de ~1.0 unidades de "
                    "pMIC (RMSE), es decir, alrededor de un orden de magnitud en potencia. "
                    "Es un modelo de cribado grueso, no de prediccion fina.",
                    "- Lo que el sistema NO predice en ningun caso: eficacia clinica, dosis "
                    "terapeutica, farmacocinetica, toxicidad, ni evolucion de la resistencia "
                    "en un paciente. Un MIC bajo in vitro no implica que el farmaco funcione "
                    "en la practica clinica.",
                ]
            ),
            metadata={
                "evidence_class": "methodology",
                "source": "eskapeguard",
                "pathogen": "",
                "citation": "EskapeGuard · frontera molecular/clinica del sistema",
                "source_url": "https://github.com/julenmg/AI4Devs-finalproject",
                "n_records": 0,
                "in_dti_test_split": False,
                "holdout_fase7": False,
            },
        )
    )
    return docs


# ---------------------------------------------------------------------------
# Orquestacion


def build_corpus(pathogens: list[str] | None = None, verbose: bool = True) -> list[EvidenceDoc]:
    """Construye el corpus completo a partir de los CSV de data/.

    La literatura de PubMed NO se incluye aqui: vive en literature.py y se
    engancha en scripts/build_index.py con el flag --with-literature, para que
    el corpus principal no dependa de la red.
    """
    pathogens = pathogens or settings.pathogens
    test_keys_by_pathogen = _load_test_inchikeys()
    docs: list[EvidenceDoc] = []
    curated_by_pathogen: dict[str, pd.DataFrame] = {}

    for pathogen in pathogens:
        curated = pd.read_csv(settings.data_processed_dir / f"curated_{_slug(pathogen)}.csv")
        curated_by_pathogen[pathogen] = curated
        test_keys = test_keys_by_pathogen.get(pathogen, set())

        chembl_meta = _chembl_compound_meta(pathogen)
        coadd_meta_df = _coadd_compound_meta(pathogen)
        coadd_meta = coadd_meta_df.to_dict("index")

        # Un compuesto merece ficha propia si aporta senal de potencia (medida o
        # acotada) o si es hit. Los demas solo tienen cribado a concentracion
        # unica y van al agregado por libreria.
        eligible_keys = set(
            curated.loc[
                (curated["assay_measure"] != INHIBITION_ONLY) | curated["is_hit"], "inchikey"
            ]
        )

        grouped: dict[str, list[dict]] = {}
        for row in curated.to_dict("records"):
            if row["inchikey"] in eligible_keys:
                grouped.setdefault(row["inchikey"], []).append(row)

        n_before = len(docs)
        for inchikey, rows in grouped.items():
            docs.append(
                _compound_card(pathogen, inchikey, rows, chembl_meta, coadd_meta, test_keys)
            )
        n_cards = len(docs) - n_before

        leftover = curated[
            (~curated["inchikey"].isin(eligible_keys)) & (curated["source"] == "coadd")
        ]
        screen_docs = _screen_summary_docs(pathogen, leftover, coadd_meta_df)
        docs += screen_docs

        binding_docs = _binding_docs(pathogen, test_keys)
        docs += binding_docs

        if verbose:
            print(
                f"[corpus:{pathogen}] {n_cards} fichas de compuesto, "
                f"{len(screen_docs)} agregados de cribado ({len(leftover)} filas resumidas), "
                f"{len(binding_docs)} fichas de binding"
            )

    background = _background_docs()
    methodology = _methodology_docs(curated_by_pathogen)
    docs += background + methodology
    if verbose:
        print(
            f"[corpus] {len(background)} fichas de patogeno, "
            f"{len(methodology)} fichas de metodologia. TOTAL: {len(docs)} documentos"
        )
    return docs


# ---------------------------------------------------------------------------
# Indice de nombres para busqueda lexica


def compound_name_index(docs: list[EvidenceDoc]) -> dict[str, list[str]]:
    """Nombre de compuesto (en minusculas) -> doc_ids de sus fichas.

    Existe porque localizar "meropenem" entre 21 000 fichas es una tarea LEXICA,
    no semantica: ningun modelo de embeddings garantiza que la ficha del
    compuesto nombrado salga en el top-k cuando hay miles de fichas con la misma
    forma. Se resuelve con una consulta exacta por metadata antes de la busqueda
    vectorial. Cubre los 648 compuestos nombrados de Kp y los 416 de Ab.
    """
    index: dict[str, list[str]] = {}
    for doc in docs:
        name = doc.metadata.get("compound_name", "")
        if not name or name.startswith("("):
            continue
        index.setdefault(name.lower(), []).append(doc.doc_id)
    return index
