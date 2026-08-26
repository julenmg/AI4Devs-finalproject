"""Fase 1 - Descarga los datos abiertos de CO-ADD (positivos y negativos reales)
frente a los patogenos ESKAPE elegidos.

Fuente: descarga bulk publica de https://db.co-add.org/downloads (release
r03, 02-2020 - la mas reciente publicada a fecha de escritura). Trae dos
ficheros dentro de un unico zip:

- Inhibition data: cribado primario a concentracion unica. Es la parte que
  hace a CO-ADD valioso frente a otros datasets de bioactividad: incluye
  compuestos con actividad Y sin ella (negativos reales), no solo positivos.
- Dose response data: seguimiento confirmatorio (MIC) de los compuestos
  activos en el cribado primario.

Ambos se filtran por organismo y se vuelcan a data/raw/coadd_<tipo>_<patogeno>.csv.

Nota de seguridad (TLS): el servidor de db.co-add.org no envia su
certificado intermedio en el handshake (cadena incompleta), asi que la
verificacion TLS falla con los almacenes de CA de sistema/certifi por
defecto. En vez de desactivar la verificacion (verify=False), se completa la
cadena bajando el intermedio real via la URL de Authority Information Access
del propio certificado hoja y verificando contra ese bundle - la
verificacion de identidad del servidor se mantiene intacta.
"""
from __future__ import annotations

import re
import ssl
import zipfile
from pathlib import Path

import certifi
import pandas as pd
import requests

from app.config import settings

COADD_DOWNLOAD_URL = (
    "https://db.co-add.org/javax.faces.resource/"
    "CO-ADD_r03.02-2020_CSV.zip.xhtml?ln=files"
)
INHIBITION_CSV_NAME = "CO-ADD_InhibitionData_r03_01-02-2020_CSV.csv"
DOSE_RESPONSE_CSV_NAME = "CO-ADD_DoseResponseData_r03_01-02-2020_CSV.csv"

# Intermedio real de db.co-add.org, obtenido de la extension AIA de su
# certificado hoja (CA Issuers). DigiCert lo mantiene vivo durante toda la
# vida del certificado hoja.
_MISSING_INTERMEDIATE_URL = (
    "http://cacerts.digicert.com/DigiCertGlobalG2TLSRSASHA2562020CA1-1.crt"
)
_CHUNK_ROWS = 100_000
REQUEST_TIMEOUT = 60


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _ca_bundle_with_missing_intermediate() -> str:
    """certifi + el intermedio que db.co-add.org deberia mandar y no manda."""
    cache_path = settings.data_raw_dir / ".cache" / "co_add_ca_bundle.pem"
    if cache_path.exists():
        return str(cache_path)

    resp = requests.get(_MISSING_INTERMEDIATE_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    intermediate_pem = ssl.DER_cert_to_PEM_cert(resp.content)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = Path(certifi.where()).read_text() + "\n" + intermediate_pem
    cache_path.write_text(bundle)
    return str(cache_path)


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.exceptions.SSLError:
        resp = session.get(
            url, timeout=REQUEST_TIMEOUT, verify=_ca_bundle_with_missing_intermediate(), **kwargs
        )
    resp.raise_for_status()
    return resp


def _download_zip(session: requests.Session) -> Path:
    zip_path = settings.data_raw_dir / ".cache" / "CO-ADD_r03.02-2020_CSV.zip"
    if zip_path.exists():
        return zip_path

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    resp = _get(session, COADD_DOWNLOAD_URL, stream=True)
    tmp_path = zip_path.with_suffix(".zip.tmp")
    with open(tmp_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
    tmp_path.rename(zip_path)
    return zip_path


def _filter_member(zf: zipfile.ZipFile, member_name: str, label: str, pathogens: list[str]) -> None:
    wanted = {p.strip().lower(): p for p in pathogens}
    matches: dict[str, list[pd.DataFrame]] = {p: [] for p in pathogens}

    with zf.open(member_name) as fh:
        for chunk in pd.read_csv(fh, index_col=0, chunksize=_CHUNK_ROWS, low_memory=False):
            organism_lower = chunk["ORGANISM"].str.lower()
            hit = chunk[organism_lower.isin(wanted.keys())]
            if hit.empty:
                continue
            for lowered, original in wanted.items():
                sub = hit[organism_lower.loc[hit.index] == lowered]
                if not sub.empty:
                    matches[original].append(sub)

    for pathogen, frames in matches.items():
        if not frames:
            print(f"[coadd] sin filas de {label} para '{pathogen}'")
            continue
        df = pd.concat(frames, ignore_index=True)
        out_path = settings.data_raw_dir / f"coadd_{label}_{_slug(pathogen)}.csv"
        df.to_csv(out_path, index=False)
        print(f"[coadd] {pathogen} ({label}): {len(df)} filas -> {out_path}")


def download_coadd_data(pathogens: list[str]) -> None:
    settings.data_raw_dir.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        zip_path = _download_zip(session)

    with zipfile.ZipFile(zip_path) as zf:
        _filter_member(zf, INHIBITION_CSV_NAME, "inhibition", pathogens)
        _filter_member(zf, DOSE_RESPONSE_CSV_NAME, "dose_response", pathogens)
