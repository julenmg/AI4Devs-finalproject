"""Fase 6 - Tests del agente y del universo de cribado.

Sin red, sin LLM y sin GPU: se comprueban las invariantes que sostienen la
honestidad del caso de estudio (separacion prediccion/medida, marcas de
contaminacion del entrenamiento, terminologia) y el detector de manipulacion de
predicciones, que es la version mecanica de la ADVERTENCIA PARA FASE 6 de
docs/decisions.md.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.config import settings
from app.generation.agentic.agent import SYSTEM_PROMPT, verify_predictions
from app.generation.agentic.screening import (
    BUCKET_DISAGREEMENT,
    BUCKET_HYPOTHESIS,
    BUCKET_NEGATIVE_AGREEMENT,
    BUCKET_RECOVERY,
    CLINICAL_LIBRARY,
    EV_MIC,
    EV_NONE,
    EV_SCREEN,
    SCREEN_CLINICAL,
    SCREEN_TRANSFER,
    assign_bucket,
    build_screen_a,
    build_screen_b,
)
from app.generation.agentic.tools import TOOL_SCHEMAS


# ---------------------------------------------------------------------------
# Universo de cribado


@pytest.fixture(scope="module")
def screen_a() -> list:
    return build_screen_a(settings.pathogens[0])


def test_el_cribado_a_son_los_700_de_la_coleccion_clinica(screen_a):
    assert len(screen_a) == 700
    assert all(c.screen == SCREEN_CLINICAL for c in screen_a)
    assert all(c.smiles for c in screen_a)


def test_el_cribado_a_no_excluye_el_holdout_pero_lo_marca(screen_a):
    """Excluir el hold-out dejaria fuera los unicos compuestos con evidencia real
    y sin contaminacion. Se incluyen y se etiquetan."""
    holdout = [c for c in screen_a if c.in_dti_test_split]
    assert holdout, "el hold-out de Fase 3 deberia tocar la coleccion clinica"
    # y ninguno del hold-out puede estar marcado como visto en entrenamiento
    assert not any(c.seen_in_training for c in holdout)


def test_solo_los_compuestos_con_mic_pudieron_verse_en_entrenamiento(screen_a):
    """El LoRA v1 solo entreno con filas de potencia exacta o acotada: un
    compuesto que solo tiene cribado a concentracion unica nunca lo vio."""
    for c in screen_a:
        if c.evidence_level == EV_SCREEN:
            assert not c.seen_in_training, c.compound_name


def test_el_cribado_b_no_tiene_ninguna_medida_en_el_patogeno_diana():
    """Es lo que hace no arbitraria la separacion entre hipotesis y desacuerdo:
    la pertenencia la decide la AUSENCIA de medida, no un umbral."""
    for pathogen in settings.pathogens:
        candidatos = build_screen_b(pathogen)
        assert candidatos
        for c in candidatos:
            assert c.screen == SCREEN_TRANSFER
            assert c.evidence_level == EV_NONE
            assert c.is_hit_real is False
            assert c.inhib_ave is None and c.px_real is None
            assert c.source_pathogen and c.source_pathogen != pathogen
            assert c.source_px is not None  # evidencia real en el otro patogeno


def test_los_dos_cribados_no_se_solapan():
    pathogen = settings.pathogens[0]
    a = {c.inchikey for c in build_screen_a(pathogen)}
    b = {c.inchikey for c in build_screen_b(pathogen)}
    assert not (a & b)


# ---------------------------------------------------------------------------
# Clasificacion en cubos


def _row(**kwargs) -> pd.Series:
    base = {
        "screen": SCREEN_CLINICAL, "is_hit_real": False, "pred_pmic": 4.0,
        "evidence_level": EV_SCREEN,
    }
    return pd.Series({**base, **kwargs})


def test_los_cubos_se_deciden_por_la_evidencia_no_por_la_prediccion():
    # un activo confirmado va a recuperacion aunque el modelo lo puntue bajo:
    # si dependiera de la prediccion, el cubo no podria usarse para validar
    assert assign_bucket(_row(is_hit_real=True, pred_pmic=2.0), 5.0) == BUCKET_RECOVERY
    assert assign_bucket(_row(is_hit_real=True, pred_pmic=9.0), 5.0) == BUCKET_RECOVERY


def test_desacuerdo_y_concordancia_negativa_se_separan_por_la_prediccion():
    assert assign_bucket(_row(pred_pmic=6.0), 5.0) == BUCKET_DISAGREEMENT
    assert assign_bucket(_row(pred_pmic=4.9), 5.0) == BUCKET_NEGATIVE_AGREEMENT


def test_todo_el_cribado_b_es_hipotesis_de_transferencia():
    fila = _row(screen=SCREEN_TRANSFER, evidence_level=EV_NONE, pred_pmic=1.0)
    assert assign_bucket(fila, 5.0) == BUCKET_HYPOTHESIS


# ---------------------------------------------------------------------------
# Independencia del DTI


def test_detecta_una_prediccion_alterada():
    """La comprobacion mecanica de la advertencia de Fase 5: si el modelo cuadra
    su prediccion con un valor real recien leido, tiene que quedar registrado."""
    calls = [{"result": {"pmic_predicho": 5.42}}]
    r = verify_predictions("El pMIC predicho es 7.10, ajustado al Ki real.", calls)
    assert r["predicciones_alteradas"] == ["7.10"]
    assert r["ok"] is False


def test_no_marca_una_prediccion_citada_fielmente():
    calls = [{"result": {"pmic_predicho": 5.42}}]
    assert verify_predictions("El pMIC predicho es 5.42.", calls)["ok"]
    # y tolera el redondeo al citar
    assert verify_predictions("El pMIC predicho es 5.4.", calls)["ok"]


def test_no_confunde_una_medida_real_con_una_prediccion():
    calls = [{"result": {"pmic_predicho": 5.42}}]
    r = verify_predictions("pMIC predicho 5.42; el pMIC real medido es 7.10.", calls)
    assert r["ok"], r


def test_no_marca_las_pmic_medidas_que_el_agente_cita_del_rag():
    """Regresion de un falso positivo real de la primera ejecucion de la
    bateria: el agente citaba MIC medidas frente al otro patogeno y el
    verificador las marcaba como predicciones alteradas. Un verificador que
    marca respuestas correctas no sirve como metrica de Fase 7."""
    calls = [{"result": {"candidatos": [{"pred_pmic": 6.093}, {"pred_pmic": 6.070}]}}]
    texto = (
        "| 1 | Sparfloxacina | 6.093 (prediccion del modelo) | MIC medida frente a "
        "K. pneumoniae: rango 0.05-32 ug/mL (pMIC 4.09-6.89), clasificada HIT |\n"
        "| 2 | Cetefloxacina | 6.070 (prediccion del modelo) | MIC medida frente a "
        "K. pneumoniae: 0.01-0.06 ug/mL (pMIC 6.83-7.43), HIT |"
    )
    r = verify_predictions(texto, calls)
    assert r["ok"], r["predicciones_alteradas"]


def test_detecta_el_ajuste_aunque_se_mencione_el_valor_real_despues():
    """El caso que la comprobacion existe para cazar menciona SIEMPRE el valor
    real justo despues ("ajustado al Ki real"). Por eso la ventana de la marca
    de medida solo mira hacia atras: si mirase hacia delante, descartaria como
    ambiguo justamente el unico caso peligroso."""
    calls = [{"result": {"pmic_predicho": 5.42}}]
    r = verify_predictions("El pMIC predicho es 7.10, ajustado al Ki real.", calls)
    assert r["predicciones_alteradas"] == ["7.10"]
    assert r["ok"] is False


def test_recoge_las_predicciones_del_cribado_tambien():
    calls = [{"result": {"candidatos": [{"pred_pmic": 6.11}, {"pred_pmic": 4.02}]}}]
    assert verify_predictions("Potencia predicha de 6.11 [ok]", calls)["ok"]
    assert not verify_predictions("Potencia predicha de 8.88", calls)["ok"]


# ---------------------------------------------------------------------------
# Herramientas y prompt


def test_hay_exactamente_tres_herramientas_con_esquema_valido():
    nombres = {t["name"] for t in TOOL_SCHEMAS}
    assert nombres == {"retrieve_evidence", "predict_affinity", "consultar_cribado"}
    for t in TOOL_SCHEMAS:
        assert t["description"] and t["input_schema"]["type"] == "object"
        assert t["input_schema"].get("required")


def test_el_prompt_prohibe_llamar_aprobados_a_los_compuestos():
    """Terminologia consistente: la libreria agrupa compuestos que alcanzaron
    fase clinica, que no es lo mismo que estar aprobado hoy."""
    assert "coleccion clinica" in SYSTEM_PROMPT
    assert "Nunca los\n   llames \"farmacos aprobados\"" in SYSTEM_PROMPT


def test_el_prompt_fija_la_frontera_y_la_independencia_del_dti():
    for exigido in (
        "NUNCA ajustes",
        "REPORTA LA DIVERGENCIA",
        "no es afinidad de union",
        "eficacia clinica",
        "seen_in_training",
    ):
        assert exigido.lower() in SYSTEM_PROMPT.lower(), exigido


def test_la_libreria_de_cribado_es_la_coleccion_clinica_del_nih():
    assert CLINICAL_LIBRARY == "NIH (USA) - Clinical Collection"
    assert EV_MIC != EV_SCREEN != EV_NONE
