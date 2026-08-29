"""Fase 5 - Tests del corpus RAG.

Sin red y sin LLM: comprueban las invariantes que sostienen la trazabilidad y
la frontera molecular/clinica, que es lo que puede romperse en silencio al
editar las plantillas. La calidad del retrieval se valida aparte, con la
bateria de preguntas de scripts/rag_demo.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from app.generation.rag.chunking import MAX_CHARS, chunk_documents
from app.generation.rag.corpus import (
    EvidenceDoc,
    _compound_card,
    _is_missing,
    build_corpus,
)
from app.generation.rag.retrieval import (
    detect_pathogen,
    format_evidence,
    normalize_compound_name,
    verify_answer,
)

REQUIRED_METADATA = {
    "evidence_class",
    "pathogen",
    "citation",
    "source_url",
    "in_dti_test_split",
    "holdout_fase7",
}


@pytest.fixture(scope="module")
def corpus() -> list[EvidenceDoc]:
    return build_corpus(verbose=False)


def test_corpus_no_esta_vacio_y_cubre_las_cinco_clases(corpus):
    classes = {d.metadata["evidence_class"] for d in corpus}
    assert classes == {
        "phenotypic_potency",
        "primary_screen_summary",
        "binding_specific",
        "background",
        "methodology",
    }
    assert len(corpus) > 30_000


def test_todo_documento_es_citable(corpus):
    """Sin cita no hay trazabilidad: un chunk sin `citation` produciria una
    afirmacion que el usuario no puede verificar."""
    for doc in corpus:
        missing = REQUIRED_METADATA - set(doc.metadata)
        assert not missing, f"{doc.doc_id} sin metadata {missing}"
        assert doc.metadata["citation"], f"{doc.doc_id} sin cita"
        assert doc.text.strip(), f"{doc.doc_id} vacio"


def test_doc_ids_unicos(corpus):
    ids = [d.doc_id for d in corpus]
    assert len(ids) == len(set(ids))


def test_las_66_filas_de_binding_van_marcadas_como_holdout(corpus):
    binding = [d for d in corpus if d.metadata["evidence_class"] == "binding_specific"]
    assert len(binding) == 66
    assert all(d.metadata["holdout_fase7"] for d in binding)
    # y son las unicas: marcar de mas dejaria a Fase 7 sin evidencia que usar
    otros = [d for d in corpus if d.metadata["evidence_class"] != "binding_specific"]
    assert not any(d.metadata["holdout_fase7"] for d in otros)


def test_el_holdout_del_dti_queda_marcado(corpus):
    """Si nadie marca los compuestos del test de Fase 3, el agente de Fase 6
    puede leer el MIC real en vez de predecirlo y Fase 7 no lo detectaria."""
    marcados = [d for d in corpus if d.metadata["in_dti_test_split"]]
    assert len(marcados) > 1000


def test_las_fichas_de_potencia_no_afirman_eficacia_clinica(corpus):
    # solo formas AFIRMATIVAS: "eficacia clinica" a secas aparece en la nota de
    # frontera, que existe precisamente para negarla.
    prohibidas = ("es eficaz", "eficaz contra", "demuestra eficacia", "cura la ",
                  "tratamiento recomendado", "funciona contra")
    fichas = [d for d in corpus if d.metadata["evidence_class"] == "phenotypic_potency"]
    for doc in fichas[:2000]:
        lowered = doc.text.lower()
        for termino in prohibidas:
            assert termino not in lowered, f"{doc.doc_id} afirma {termino!r}"


def test_toda_ficha_de_potencia_lleva_la_frontera_explicita(corpus):
    fichas = [d for d in corpus if d.metadata["evidence_class"] == "phenotypic_potency"]
    for doc in fichas[:2000]:
        assert "No es afinidad de union" in doc.text
        assert "eficacia clinica" in doc.text  # solo aparece para negarla


def test_valor_censurado_se_redacta_como_cota_no_como_inactivo():
    """Regresion de la regla mas importante de la plantilla: '>128' significa
    'no se demostro actividad hasta esa dosis', nunca 'inactivo'."""
    rows = [
        {
            "source": "chembl",
            "compound_id": "CHEMBL1",
            "smiles": "CCO",
            "inchikey": "AAAAAAAAAAAAAA-UHFFFAOYSA-N",
            "assay_measure": "MIC",
            "raw_value": 128.0,
            "raw_unit": "ug.mL-1",
            "relation": ">",
            "censored": True,
            "pX": 3.5,
            "is_hit": False,
            "document_year": 2020.0,
        }
    ]
    card = _compound_card("Klebsiella pneumoniae", rows[0]["inchikey"], rows, {}, {}, set())
    assert "no se observo inhibicion hasta" in card.text
    # 'inactivo' solo puede aparecer negado, nunca como afirmacion sobre el compuesto
    lowered = card.text.lower()
    assert "es inactivo" not in lowered
    assert "compuesto inactivo" not in lowered
    assert "no 'inactivo'" in lowered
    assert card.metadata["censored_only"] is True
    assert "best_pX" not in card.metadata  # una cota superior no es potencia medida


def test_valor_exacto_produce_best_px():
    rows = [
        {
            "source": "chembl",
            "compound_id": "CHEMBL1",
            "smiles": "CCO",
            "inchikey": "BBBBBBBBBBBBBB-UHFFFAOYSA-N",
            "assay_measure": "MIC",
            "raw_value": 0.5,
            "raw_unit": "ug.mL-1",
            "relation": "=",
            "censored": False,
            "pX": 6.2,
            "is_hit": True,
            "document_year": 2020.0,
        }
    ]
    card = _compound_card("Klebsiella pneumoniae", rows[0]["inchikey"], rows, {}, {}, set())
    assert card.metadata["best_pX"] == 6.2
    assert card.metadata["is_hit"] is True
    assert "HIT" in card.text


def test_el_chunking_no_parte_las_fichas_estructuradas(corpus):
    chunks = chunk_documents(corpus)
    # una ficha estructurada nunca se trocea: partirla mezclaria dos compuestos
    assert len(chunks) == len(corpus)
    assert all(c.metadata["n_chunks"] == 1 for c in chunks)


def test_el_chunking_trocea_un_documento_largo_heredando_la_cita():
    largo = EvidenceDoc(
        doc_id="pubmed:123",
        text="Articulo cientifico (PubMed)\nTitulo: X\n\nAbstract:\n" + ("frase larga. " * 400),
        metadata={"evidence_class": "literature", "citation": "PMID 123", "pmid": "123"},
    )
    chunks = chunk_documents([largo])
    assert len(chunks) > 1
    assert all(c.metadata["citation"] == "PMID 123" for c in chunks)
    assert all(len(c.text) <= MAX_CHARS * 2 for c in chunks)  # cabecera repetida


def test_deteccion_de_patogeno_es_determinista():
    assert detect_pathogen("mecanismos de Klebsiella pneumoniae") == "Klebsiella pneumoniae"
    assert detect_pathogen("resistencia en A. baumannii") == "Acinetobacter baumannii"
    # dos patogenos -> sin filtro, una comparacion necesita ver ambos
    assert detect_pathogen("compara Klebsiella con Acinetobacter") is None
    assert detect_pathogen("que es la resistencia antimicrobiana") is None


def test_verify_answer_detecta_cita_inventada():
    hits = [{"text": "MIC medida 0.5 ug/mL", "metadata": {"citation": "ChEMBL X"}}]
    result = verify_answer("Segun [E1] el MIC es 0.5 ug/mL, y [E7] lo confirma.", hits)
    assert result["invalid_labels"] == ["E7"]
    assert result["citations_ok"] is False


def test_verify_answer_detecta_numero_no_respaldado():
    hits = [{"text": "MIC medida 0.5 ug/mL", "metadata": {"citation": "ChEMBL X"}}]
    result = verify_answer("El MIC es 0.5 ug/mL [E1] y el pKd 8.42 [E1].", hits)
    assert result["ungrounded_numbers"] == ["8.42"]
    assert result["citations_ok"] is True  # la cita existe; el numero es solo aviso


def test_verify_answer_tolera_redondeo_y_conteos():
    hits = [{"text": "MIC 0.0312 ug/mL en 12 registros", "metadata": {"citation": "ChEMBL X"}}]
    result = verify_answer("Hay 3 fichas [E1]; el MIC ronda 0.0313 ug/mL [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_el_bloque_de_evidencia_va_etiquetado_y_con_cita():
    hits = [
        {"text": "contenido", "metadata": {"citation": "PMID 1", "source_url": "u",
                                           "evidence_class": "literature"}},
    ]
    block = format_evidence(hits)
    assert block.startswith("[E1] Referencia: PMID 1")
    assert "Clase de evidencia: literature" in block


def test_ningun_nombre_de_compuesto_es_nan(corpus):
    """Regresion: bool(float('nan')) es True, asi que un COMPOUND_NAME vacio de
    CO-ADD colaba como nombre y 8 295 fichas decian "Compuesto: Nan"."""
    for doc in corpus:
        name = doc.metadata.get("compound_name", "")
        assert name.lower() not in {"nan", "none", "null"}, f"{doc.doc_id}: {name!r}"
        # linea exacta, no substring: "Nanaomycin" es un compuesto real
        assert "\nCompuesto: Nan\n" not in "\n" + doc.text + "\n"


def test_las_fichas_sin_nombre_lo_dicen_explicitamente(corpus):
    fichas = [d for d in corpus if d.metadata["evidence_class"] == "phenotypic_potency"]
    anonimas = [d for d in fichas if not d.metadata["compound_name"]]
    assert anonimas, "el corpus deberia tener compuestos sin nombre asignado"
    for doc in anonimas[:500]:
        assert "(sin nombre asignado en las fuentes)" in doc.text


def test_existe_un_agregado_global_de_cribado_por_patogeno(corpus):
    """El desglose por libreria sin el agregado global obliga al modelo a sumar
    solo las librerias que quepan en el top-k, dando un total parcial."""
    globales = [d for d in corpus if d.doc_id.endswith(":global")]
    assert len(globales) == 2  # un patogeno cada uno
    for doc in globales:
        assert doc.metadata["evidence_class"] == "primary_screen_summary"
        assert doc.metadata["n_records"] > 70_000
        assert "Total de compuestos cribados" in doc.text


def test_verify_answer_acepta_separador_de_miles_espanol():
    """"1.859" en espanol es 1859; leerlo como 1.859 marcaba como no respaldada
    cada cifra que el modelo escribia con separador de miles."""
    hits = [{"text": "Compuestos cribados: 1859 de 96069 filas", "metadata": {}}]
    result = verify_answer("Se cribaron 1.859 compuestos de 96.069 [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_verify_answer_sigue_detectando_una_cifra_inventada_con_puntos():
    hits = [{"text": "Compuestos cribados: 1859", "metadata": {}}]
    result = verify_answer("Se cribaron 7.412 compuestos [E1].", hits)
    assert result["ungrounded_numbers"] == ["7.412"]


def test_ningun_valor_ausente_se_escapa_como_texto(corpus):
    """Barrido sistematico del patron que produjo "Compuesto: Nan".

    bool(float("nan")) es True, asi que cualquier `if valor:` sobre un campo de
    pandas deja pasar un NaN a la plantilla. Este test recorre TODO el texto
    generado, el search_text y la metadata buscando el literal, para que un
    campo nuevo mal guardado falle aqui y no en una respuesta del modelo.
    """
    pat = re.compile(r"\bna[nt]\b|\bnone\b|\bnull\b|\b<na>\b", re.IGNORECASE)
    for doc in corpus:
        for campo, contenido in (
            ("text", doc.text),
            ("search_text", doc.search_text or ""),
            ("metadata", str(doc.metadata)),
        ):
            match = pat.search(contenido)
            assert not match, (
                f"{doc.doc_id} filtra {match.group(0)!r} en {campo}: "
                f"...{contenido[max(0, match.start() - 60):match.start() + 20]}..."
            )


def test_is_missing_cubre_las_formas_reales_de_ausencia():
    assert _is_missing(None)
    assert _is_missing(float("nan"))
    assert _is_missing(pd.NA)
    assert _is_missing("")
    assert _is_missing("   ")
    assert _is_missing("nan")      # llega asi tras un .astype(str) aguas arriba
    assert _is_missing("NaN")
    assert _is_missing("None")
    # y no marca como ausente lo que si es un valor
    assert not _is_missing("Nanaomycin")
    assert not _is_missing(0)
    assert not _is_missing(0.0)
    assert not _is_missing("0")
    assert not _is_missing("ATCC 700603; MDR")


# ---------------------------------------------------------------------------
# Normalizacion ES->EN del atajo lexico


@pytest.mark.parametrize(
    "espanol, ingles",
    [
        # el caso que motivo el fix: la consulta va en espanol, el indice
        # guarda el nombre en ingles tal cual lo da ChEMBL
        ("ciprofloxacino", "CIPROFLOXACIN"),
        # vocal final distinta a cada lado (-a / -e)
        ("cefotaxima", "CEFOTAXIME"),
        # y->i y consonante doble: -micina / -mycin, -cilina / -cillin
        ("vancomicina", "VANCOMYCIN"),
        ("amoxicilina", "AMOXICILLIN"),
        # digrafos que el espanol simplifica: ph->f, th->t
        ("cefalexina", "CEPHALEXIN"),
        ("azitromicina", "AZITHROMYCIN"),
        # -ciclina / -cycline combina y->i con vocal final
        ("tetraciclina", "TETRACYCLINE"),
    ],
)
def test_la_normalizacion_une_las_formas_espanola_e_inglesa(espanol, ingles):
    assert normalize_compound_name(espanol) == normalize_compound_name(ingles)


def test_los_nombres_iguales_en_ambos_idiomas_siguen_funcionando():
    """Control de no-regresion: meropenem ya coincidia por igualdad exacta antes
    del fix y tiene que seguir coincidiendo despues."""
    for nombre in ("meropenem", "imipenem", "aztreonam", "ertapenem"):
        assert normalize_compound_name(nombre) == normalize_compound_name(nombre.upper())
        assert normalize_compound_name(nombre) == nombre


def test_la_normalizacion_no_funde_farmacos_distintos():
    """La normalizacion pierde informacion a proposito; lo que no puede hacer es
    juntar dos compuestos que no son el mismo."""
    distintos = [
        "ciprofloxacin",
        "levofloxacin",
        "norfloxacin",
        "cefotaxime",
        "cefepime",
        "ceftazidime",
        "meropenem",
        "imipenem",
        "ertapenem",
        "tetracycline",
        "doxycycline",
        "minocycline",
    ]
    normalizados = [normalize_compound_name(n) for n in distintos]
    assert len(set(normalizados)) == len(distintos)


def test_la_normalizacion_es_inyectiva_sobre_el_indice_real():
    """Barrido de los 717 nombres indexados: ninguna pareja de compuestos
    distintos puede caer en la misma forma normalizada, o el atajo lexico
    devolveria la ficha equivocada con distancia 0.0 (peor que no encontrarla)."""
    ruta = Path("data/chroma_db/compound_names.json")
    if not ruta.exists():
        pytest.skip("indice no construido; se ejecuta build_index primero")
    nombres = json.loads(ruta.read_text())
    grupos: dict[str, list[str]] = {}
    for nombre in nombres:
        clave = normalize_compound_name(nombre)
        if len(clave) < 5:
            continue  # siglas cortas, excluidas tambien antes del fix
        grupos.setdefault(clave, []).append(nombre)
    colisiones = {k: v for k, v in grupos.items() if len(v) > 1}
    assert not colisiones, f"formas normalizadas compartidas: {colisiones}"
