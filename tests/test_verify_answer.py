"""Fase 5 - Tests de regresion del extractor de numeros de verify_answer.

Fichero aparte porque esta funcion no es solo una comprobacion interna del RAG:
es la base de la metrica objetiva de Fase 7 ("el agente no inventa cifras"). Si
el parseo de numeros falla, la metrica miente en las dos direcciones — marcando
cifras correctas como inventadas (paso dos veces en la bateria de validacion) o,
peor, dejando pasar una inventada.

Sin red y sin LLM: se le pasa una respuesta ya escrita y una evidencia fijada.
"""
from __future__ import annotations

import pytest

from app.generation.rag.retrieval import _number_variants, _numbers_in, verify_answer


def _hits(*textos: str) -> list[dict]:
    return [{"text": t, "metadata": {"citation": "fuente de prueba"}} for t in textos]


# ---------------------------------------------------------------------------
# Extraccion cruda de numeros


@pytest.mark.parametrize(
    "texto, esperado",
    [
        # rangos: el guion es separador, no signo. Leerlo como menos metia -6.7
        # en el contexto y dejaba fuera 6.7 -> falso positivo (fallo real).
        ("pMIC 3.1-6.7", ["3.1", "6.7"]),
        ("rango 0.01-512 ug/mL", ["0.01", "512"]),
        ("entre 1859-1973 compuestos", ["1859", "1973"]),
        # negativos de verdad: la inhibicion mediana negativa existe en CO-ADD
        ("inhibicion mediana: -3%", ["-3"]),
        ("valores de -3.5 a 12", ["-3.5", "12"]),
        # decimales y notacion cientifica
        ("pMIC 4.9812", ["4.9812"]),
        ("6.4e+04 nM", ["6.4e+04"]),
        # porcentajes
        ("85.3% de inhibicion", ["85.3"]),
        ("0.00% de hits", ["0.00"]),
        # separador de miles (el texto se normaliza a punto antes de extraer)
        ("96.069 filas", ["96.069"]),
        ("1,859 compuestos", ["1.859"]),
    ],
)
def test_extraccion_de_numeros(texto, esperado):
    assert _numbers_in(texto) == esperado


# ---------------------------------------------------------------------------
# Lecturas posibles de un numero


def test_el_separador_de_miles_admite_las_dos_lecturas():
    """"1.859" es ambiguo sin contexto: mil ochocientos cincuenta y nueve en
    espanol, uno coma ocho cincuenta y nueve en ingles. Se aceptan ambas."""
    assert _number_variants("1.859") == {1.859, 1859.0}
    assert _number_variants("96.069") == {96.069, 96069.0}


def test_un_decimal_normal_solo_tiene_una_lectura():
    assert _number_variants("4.98") == {4.98}
    assert _number_variants("-3.5") == {-3.5}
    assert _number_variants("512") == {512.0}


# ---------------------------------------------------------------------------
# Comportamiento de verify_answer


def test_rango_citado_no_se_marca_como_inventado():
    hits = _hits("MIC medida: 0.01-512 ug/mL -> pMIC 3.1-6.7 (mediana 4.98)")
    result = verify_answer("El pMIC va de 3.1 a 6.7, con mediana 4.98 [E1].", hits)
    assert result["ungrounded_numbers"] == []
    assert result["citations_ok"] is True


def test_separador_de_miles_espanol_no_se_marca_como_inventado():
    hits = _hits("Total de compuestos cribados: 96069. Sin senal apreciable: 86388.")
    result = verify_answer("Se cribaron 96.069 compuestos, 86.388 sin senal [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_porcentaje_citado_literalmente():
    hits = _hits("Con inhibicion >= 80%: 0 (0.00%). Inhibicion mediana: 12.6%, maxima: 21.6%.")
    result = verify_answer("La mediana fue del 12.6% y la maxima del 21.6% [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_valor_negativo_real_no_se_marca_como_inventado():
    """Los porcentajes de inhibicion negativos son reales en CO-ADD (ruido de
    medida por debajo del control), no un error de signo."""
    hits = _hits("Inhibicion mediana: -3%, maxima: 34.5%.")
    result = verify_answer("La inhibicion mediana fue -3% [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_redondeo_al_citar_se_tolera():
    hits = _hits("MIC 0.0312 ug/mL, pKd 8.4231")
    result = verify_answer("El MIC ronda 0.0313 y el pKd 8.42 [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_conteos_del_discurso_no_cuentan_como_cifras():
    hits = _hits("MIC 0.5 ug/mL")
    result = verify_answer("Las 3 fichas coinciden; hay 2 fuentes [E1].", hits)
    assert result["ungrounded_numbers"] == []


def test_cifra_inventada_si_se_detecta():
    hits = _hits("MIC medida 0.5 ug/mL en 12 registros de 2019")
    result = verify_answer("El MIC es 0.5 pero el pKd vale 8.42 [E1].", hits)
    assert result["ungrounded_numbers"] == ["8.42"]


def test_cifra_inventada_con_separador_de_miles_si_se_detecta():
    hits = _hits("Compuestos cribados: 1859")
    result = verify_answer("Se cribaron 7.412 compuestos [E1].", hits)
    assert result["ungrounded_numbers"] == ["7.412"]


def test_cita_inventada_es_fallo_duro_y_la_cifra_solo_aviso():
    hits = _hits("MIC 0.5 ug/mL")
    result = verify_answer("Segun [E1] y [E9], el pKd es 8.42.", hits)
    assert result["invalid_labels"] == ["E9"]
    assert result["citations_ok"] is False
    assert result["ungrounded_numbers"] == ["8.42"]


def test_las_etiquetas_de_evidencia_no_se_leen_como_cifras():
    """[E1]..[E8] son referencias, no valores: contarlas como numeros marcaria
    como inventada cualquier respuesta bien citada."""
    hits = _hits("MIC 0.5 ug/mL")
    result = verify_answer("Ver [E1], [E2], [E3], [E4], [E5], [E6], [E7], [E8].", hits)
    assert result["ungrounded_numbers"] == []
    assert result["cited_labels"] == ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"]


def test_limitacion_conocida_el_decimal_de_tres_cifras_es_ambiguo():
    """Documenta un limite aceptado, no un bug: "0.985" encaja tambien con el
    patron de separador de miles, asi que si 985 esta en la evidencia el valor
    se da por respaldado. La comprobacion es deliberadamente permisiva (es un
    aviso para revision humana, no un bloqueo), y preferimos un falso negativo
    raro a marcar cifras correctas en cada respuesta."""
    hits = _hits("Se analizaron 985 registros")
    result = verify_answer("El RMSE fue 0.985 [E1].", hits)
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# Artefactos observados en la bateria de Fase 7 (110 preguntas)


def test_notacion_cientifica_reescrita_no_es_una_cifra_inventada():
    """El modelo reescribe la evidencia en forma legible: donde la ficha dice
    2050, la respuesta escribe "2.05x10^3". Cinco de los cinco avisos del bloque
    RAG de Fase 7 eran de este tipo."""
    hits = _hits("MIC medida entre 0.05 y 2050 ug/mL, pMIC 2.43 a 7.05")
    assert verify_answer("MIC entre 0.05 y 2.05x10^3 ug/mL [E1].", hits)["ungrounded_numbers"] == []


def test_notacion_cientifica_con_superindices():
    hits = _hits("IC50 rango 80-3600 nM")
    assert verify_answer("IC50 rango 80-3.6\u00d710\u00b3 nM [E1].", hits)["ungrounded_numbers"] == []


def test_sigue_detectando_una_cifra_inventada_en_notacion_cientifica():
    hits = _hits("MIC medida 2050 ug/mL")
    assert verify_answer("El MIC es 9.9x10^3 ug/mL [E1].", hits)["ungrounded_numbers"] == ["9900.0"]
