"""Precondiciones de datos para la suite.

Los CSV de ChEMBL/CO-ADD y el dataset curado NO se versionan (pesan cientos de
MB y se regeneran con la ingesta, ver README §1.4). Sin ellos, las fixtures que
construyen el corpus o el universo de cribado fallaban con FileNotFoundError, y
un ERROR en un clon limpio se lee como "el proyecto esta roto" en vez de como
"faltan datos locales".

Esto replica para los datos el patron que los tests ya usaban para el indice
vectorial (`pytest.skip("indice no construido")`): se comprueban las
precondiciones y se salta CON UN MENSAJE ACCIONABLE, por test y no por
coleccion, y solo en los tests que de verdad tocan disco. Los que no dependen
de los datos —normalizacion de nombres, extractor de numeros, clasificacion en
cubos, esquemas de las herramientas— siguen corriendo en cualquier clon.

No se modifica ningun fichero de test: la deteccion se hace aqui, mirando qué
fixtures pide cada test y qué funciones llama su cuerpo.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.config import settings
from app.generation.rag.corpus import _slug

# Fixtures que construyen su objeto leyendo los CSV.
FIXTURES_CON_DATOS = {"corpus", "screen_a"}

# Funciones que van a disco en cuanto se las llama, aunque el test no use
# ninguna fixture.
LLAMADAS_CON_DATOS = re.compile(r"\b(build_corpus|build_screen_a|build_screen_b)\s*\(")

MENSAJE = (
    "dataset local ausente ({falta}); ejecuta la ingesta (README §1.4): "
    "uv run python -m app.ingestion.chembl_loader && "
    "uv run python -m app.ingestion.coadd_loader && "
    "uv run python -m app.ingestion.curate_dataset"
)


def _ficheros_requeridos() -> list:
    """Lo que necesitan build_corpus() y build_screen_a/b() para ambos patogenos."""
    requeridos = [settings.data_processed_dir / "split_test_inchikeys.json"]
    for pathogen in settings.pathogens:
        slug = _slug(pathogen)
        requeridos += [
            settings.data_processed_dir / f"curated_{slug}.csv",
            settings.data_raw_dir / f"chembl_{slug}.csv",
            settings.data_raw_dir / f"coadd_inhibition_{slug}.csv",
            settings.data_raw_dir / f"coadd_dose_response_{slug}.csv",
        ]
    return requeridos


def _faltantes() -> list[str]:
    return [str(p) for p in _ficheros_requeridos() if not p.exists()]


# Memo en memoria, NO el cache de pytest: ese persiste entre ejecuciones en
# .pytest_cache y devolveria el resultado de la corrida anterior, que es
# justo lo contrario de lo que se quiere comprobar.
_FALTANTES: list[str] | None = None


def _faltantes_memo() -> list[str]:
    global _FALTANTES
    if _FALTANTES is None:
        _FALTANTES = _faltantes()
    return _FALTANTES


def _necesita_datos(item: pytest.Item) -> bool:
    if FIXTURES_CON_DATOS & set(getattr(item, "fixturenames", ())):
        return True
    funcion = getattr(item, "function", None)
    if funcion is None:
        return False
    try:
        return bool(LLAMADAS_CON_DATOS.search(inspect.getsource(funcion)))
    except (OSError, TypeError):  # sin fuente disponible
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip por test, antes de que la fixture intente leer nada."""
    if not _necesita_datos(item):
        return
    falta = _faltantes_memo()
    if falta:
        pytest.skip(MENSAJE.format(falta=f"{len(falta)} fichero(s), p. ej. {falta[0]}"))
