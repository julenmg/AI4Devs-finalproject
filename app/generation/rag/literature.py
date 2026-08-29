"""Fase 5 - Literatura de apoyo: abstracts reales de PubMed.

Modulo OPCIONAL y aislado a proposito: el corpus principal (corpus.py) se
construye sin red. Aqui se engancha con el flag --with-literature de
scripts/build_index.py.

Como se evita que el sistema invente citas: la cita NUNCA la redacta el LLM.
Se construye por codigo desde el XML de PubMed (PMID, DOI, revista, ano, autor)
y viaja como metadata del chunk; el prompt de generacion solo puede citar
etiquetas [E1]..[Ek] de la evidencia que se le ha entregado, y retrieval.py
verifica a posteriori que toda etiqueta citada existe. Un PMID que no este en
data/raw/pubmed_*.json no puede aparecer en una respuesta correcta.

El JSON descargado se versiona en el repo (excepcion explicita en .gitignore):
son ~120 abstracts que solo hay que bajar una vez, y asi quien clone el repo
puede reconstruir el indice sin depender de que PubMed responda en ese momento.

AVISO DE SEGURIDAD: el texto de los abstracts es contenido externo no confiable.
Se inyecta en el prompt como dato a citar, nunca como instrucciones. Ver la
delimitacion del bloque de evidencia en retrieval.py.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET

import requests

from app.config import settings
from app.generation.rag.corpus import EvidenceDoc

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
USER_AGENT = "eskapeguard-tfm/0.1 (+https://github.com/julenmg/AI4Devs-finalproject)"
REQUEST_TIMEOUT = 60
RETMAX = 40
MIN_ABSTRACT_CHARS = 200

# Tres consultas fijas y hardcodeadas. No se generan dinamicamente ni las decide
# un LLM: un corpus reproducible exige que la busqueda sea la misma en cada
# ejecucion y quede auditable en el repo.
QUERIES: dict[str, dict] = {
    "klebsiella_resistance": {
        "term": (
            '"Klebsiella pneumoniae"[Title/Abstract] AND '
            "(carbapenem resistance[Title/Abstract] OR carbapenemase[Title/Abstract] OR "
            "KPC[Title/Abstract] OR NDM[Title/Abstract] OR OXA-48[Title/Abstract]) AND "
            '("2015"[PDAT] : "3000"[PDAT])'
        ),
        "pathogen": "Klebsiella pneumoniae",
        "topic": "mecanismos de resistencia",
    },
    "acinetobacter_resistance": {
        "term": (
            '"Acinetobacter baumannii"[Title/Abstract] AND '
            "(carbapenem resistance[Title/Abstract] OR carbapenemase[Title/Abstract] OR "
            "OXA-23[Title/Abstract] OR efflux[Title/Abstract]) AND "
            '("2015"[PDAT] : "3000"[PDAT])'
        ),
        "pathogen": "Acinetobacter baumannii",
        "topic": "mecanismos de resistencia",
    },
    "repurposing_amr": {
        "term": (
            "(drug repurposing[Title/Abstract] OR drug repositioning[Title/Abstract]) AND "
            "(antimicrobial resistance[Title/Abstract] OR antibacterial[Title/Abstract] OR "
            "Gram-negative[Title/Abstract]) AND "
            '("2015"[PDAT] : "3000"[PDAT])'
        ),
        "pathogen": "",
        "topic": "reposicionamiento de farmacos",
    },
}


def _cache_path(key: str):
    return settings.data_raw_dir / f"pubmed_{key}.json"


def _esearch(session: requests.Session, term: str) -> list[str]:
    resp = session.get(
        f"{EUTILS}/esearch.fcgi",
        params={
            "db": "pubmed",
            "term": term,
            "retmax": RETMAX,
            "sort": "relevance",
            "retmode": "json",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["esearchresult"]["idlist"]


def _text_of(node) -> str:
    """AbstractText puede venir troceado en secciones etiquetadas (BACKGROUND,
    METHODS...) y con markup inline; itertext() lo aplana sin perder nada."""
    return "".join(node.itertext()).strip()


def _efetch(session: requests.Session, pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    resp = session.post(
        f"{EUTILS}/efetch.fcgi",
        data={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    records = []
    for article in root.findall(".//PubmedArticle"):
        pmid_node = article.find(".//PMID")
        if pmid_node is None:
            continue
        abstract_parts = []
        for node in article.findall(".//Abstract/AbstractText"):
            label = node.get("Label")
            body = _text_of(node)
            abstract_parts.append(f"{label}: {body}" if label else body)
        abstract = "\n".join(p for p in abstract_parts if p)
        if len(abstract) < MIN_ABSTRACT_CHARS:
            continue  # sin abstract util no hay evidencia que citar

        title_node = article.find(".//ArticleTitle")
        journal_node = article.find(".//Journal/ISOAbbreviation")
        if journal_node is None:
            journal_node = article.find(".//Journal/Title")
        year_node = article.find(".//JournalIssue/PubDate/Year")
        if year_node is None:
            year_node = article.find(".//JournalIssue/PubDate/MedlineDate")
        doi = ""
        for eloc in article.findall(".//ELocationID"):
            if eloc.get("EIdType") == "doi":
                doi = (eloc.text or "").strip()
                break
        authors = article.findall(".//AuthorList/Author/LastName")
        first_author = authors[0].text if authors else ""

        records.append(
            {
                "pmid": pmid_node.text,
                "title": _text_of(title_node) if title_node is not None else "",
                "abstract": abstract,
                "journal": _text_of(journal_node) if journal_node is not None else "",
                "year": (year_node.text or "")[:4] if year_node is not None else "",
                "doi": doi,
                "first_author": first_author or "",
            }
        )
    return records


def fetch_literature(refresh: bool = False, verbose: bool = True) -> dict[str, list[dict]]:
    """Descarga (o lee de cache) los abstracts de las tres consultas fijas."""
    settings.data_raw_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {}

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        for key, spec in QUERIES.items():
            path = _cache_path(key)
            if path.exists() and not refresh:
                out[key] = json.loads(path.read_text())
                if verbose:
                    print(f"[pubmed:{key}] {len(out[key])} abstracts (cache {path})")
                continue

            pmids = _esearch(session, spec["term"])
            time.sleep(0.34)  # limite de NCBI sin API key: 3 peticiones/segundo
            records = _efetch(session, pmids)
            path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
            out[key] = records
            if verbose:
                print(
                    f"[pubmed:{key}] {len(pmids)} PMIDs -> {len(records)} con abstract "
                    f"utilizable -> {path}"
                )
            time.sleep(0.34)

    return out


def literature_docs(refresh: bool = False, verbose: bool = True) -> list[EvidenceDoc]:
    """Un EvidenceDoc por abstract. El troceado de los mas largos lo hace
    chunking.py; aqui el documento llega entero."""
    fetched = fetch_literature(refresh=refresh, verbose=verbose)
    docs: list[EvidenceDoc] = []
    seen: set[str] = set()

    for key, records in fetched.items():
        spec = QUERIES[key]
        for rec in records:
            if rec["pmid"] in seen:
                continue  # el mismo articulo puede salir en dos consultas
            seen.add(rec["pmid"])

            citation = f"PMID {rec['pmid']}"
            if rec["first_author"]:
                citation += f" · {rec['first_author']} et al."
            if rec["journal"]:
                citation += f" · {rec['journal']}"
            if rec["year"]:
                citation += f" {rec['year']}"

            text = "\n".join(
                [
                    f"Articulo cientifico (PubMed) - {spec['topic']}",
                    f"Titulo: {rec['title']}",
                    f"Revista: {rec['journal']} ({rec['year']}) | PMID: {rec['pmid']}"
                    + (f" | DOI: {rec['doi']}" if rec["doi"] else ""),
                    "",
                    "Abstract:",
                    rec["abstract"],
                ]
            )

            docs.append(
                EvidenceDoc(
                    doc_id=f"pubmed:{rec['pmid']}",
                    text=text,
                    metadata={
                        "evidence_class": "literature",
                        "source": "literature",
                        "pathogen": spec["pathogen"],
                        "pmid": rec["pmid"],
                        "doi": rec["doi"],
                        "journal": rec["journal"],
                        "year_min": int(rec["year"]) if rec["year"].isdigit() else 0,
                        "query_key": key,
                        "n_records": 1,
                        "citation": citation,
                        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/",
                        "in_dti_test_split": False,
                        "holdout_fase7": False,
                    },
                )
            )

    if verbose:
        print(f"[pubmed] {len(docs)} abstracts unicos tras deduplicar por PMID")
    return docs
