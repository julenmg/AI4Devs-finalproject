"""Fase 1 - Descarga y filtra ChEMBL para las dianas bacterianas elegidas.

Usa la API REST publica de ChEMBL (https://www.ebi.ac.uk/chembl/api/data,
sin API key) para bajar, por cada patogeno elegido, las bioactividades
antibacterianas reportadas contra ese organismo (MIC, IC50, Ki, Kd, EC50,
GI50) y las vuelca a CSV en data/raw/chembl_<patogeno>.csv.
"""
from __future__ import annotations

import re
import time

import pandas as pd
import requests

from app.config import settings

CHEMBL_ACTIVITY_URL = "https://www.ebi.ac.uk/chembl/api/data/activity.json"
RELEVANT_STANDARD_TYPES = ("MIC", "IC50", "EC50", "GI50", "Ki", "Kd")
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 60
USER_AGENT = "eskapeguard-tfm/0.1 (+https://github.com/julenmg/AI4Devs-finalproject)"

OUTPUT_COLUMNS = [
    "molecule_chembl_id",
    "canonical_smiles",
    "molecule_pref_name",
    "target_chembl_id",
    "target_organism",
    "assay_chembl_id",
    "assay_type",
    "standard_type",
    "standard_relation",
    "standard_value",
    "standard_units",
    "pchembl_value",
    "document_year",
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _fetch_activities(session: requests.Session, organism: str) -> list[dict]:
    """Pagina el endpoint /activity filtrando por organismo diana y por los
    standard_type relevantes para actividad antibacteriana."""
    activities: list[dict] = []
    url: str | None = CHEMBL_ACTIVITY_URL
    params: dict | None = {
        "target_organism": organism,
        "standard_type__in": ",".join(RELEVANT_STANDARD_TYPES),
        "limit": PAGE_SIZE,
        "offset": 0,
    }
    while url:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        activities.extend(payload["activities"])
        next_path = payload["page_meta"].get("next")
        url = f"https://www.ebi.ac.uk{next_path}" if next_path else None
        params = None  # el link "next" ya trae la query string completa
        if url:
            time.sleep(0.2)  # cortesia con la API publica de EBI
    return activities


def download_chembl_targets(pathogens: list[str]) -> None:
    settings.data_raw_dir.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        for pathogen in pathogens:
            activities = _fetch_activities(session, pathogen)
            if not activities:
                print(f"[chembl] sin actividades para '{pathogen}'")
                continue

            df = pd.DataFrame(activities).reindex(columns=OUTPUT_COLUMNS)
            df = df.dropna(subset=["canonical_smiles", "standard_value"])

            out_path = settings.data_raw_dir / f"chembl_{_slug(pathogen)}.csv"
            df.to_csv(out_path, index=False)
            print(f"[chembl] {pathogen}: {len(df)} filas -> {out_path}")
