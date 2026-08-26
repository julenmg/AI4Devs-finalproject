#!/usr/bin/env bash
# Scaffold de EskapeGuard — estructura inicial del TFM.
# Colócalo en la raíz del repo (ya clonado y en tu rama finalproject-[iniciales])
# y ejecuta:  bash scaffold_project.sh
set -e

echo "Creando estructura de carpetas..."

mkdir -p docs
mkdir -p data/raw data/processed
mkdir -p app/ingestion
mkdir -p app/foundation
mkdir -p app/generation/cag
mkdir -p app/generation/rag
mkdir -p app/generation/agentic
mkdir -p training
mkdir -p evals
mkdir -p scripts
mkdir -p tests

touch data/raw/.gitkeep data/processed/.gitkeep

# ---------- Paquete app ----------
touch app/__init__.py

cat > app/config.py << 'PY'
"""Configuracion central: rutas, checkpoint, patogenos elegidos, claves de API.
Fase 1-2.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    dti_checkpoint: str = "ibm-research/biomed.omics.bl.sm.ma-ted-458m.dti_bindingdb_pkd"
    pathogens: list[str] = ["Klebsiella pneumoniae"]  # ajusta a tu alcance (1-2 patogenos)
    data_raw_dir: Path = Path("data/raw")
    data_processed_dir: Path = Path("data/processed")
    anthropic_api_key: str = ""


settings = Settings()
PY

# ---------- ingestion (Fase 1) ----------
touch app/ingestion/__init__.py

cat > app/ingestion/chembl_loader.py << 'PY'
"""Fase 1 - Descarga y filtra ChEMBL para las dianas bacterianas elegidas.

TODO: implementar la consulta a la API/bulk download de ChEMBL y guardar en data/raw/.
"""


def download_chembl_targets(pathogens: list[str]) -> None:
    raise NotImplementedError
PY

cat > app/ingestion/coadd_loader.py << 'PY'
"""Fase 1 - Descarga los datos abiertos de CO-ADD (positivos y negativos reales)
frente a los patogenos ESKAPE elegidos.

TODO: implementar la descarga/parseo del dataset CO-ADD.
"""


def download_coadd_data(pathogens: list[str]) -> None:
    raise NotImplementedError
PY

cat > app/ingestion/curate_dataset.py << 'PY'
"""Fase 1 - Cruza ChEMBL + CO-ADD, limpia y arma el dataset curado
(positivos y negativos reales) que alimenta el fine-tune de la Fase 3.

TODO: implementar la union, deduplicacion y guardado en data/processed/.
"""


def build_curated_dataset() -> None:
    raise NotImplementedError
PY

# ---------- foundation (Fase 2) ----------
touch app/foundation/__init__.py

cat > app/foundation/dti_model.py << 'PY'
"""Fase 2 - Carga y valida el checkpoint DTI de IBM.

Uso previsto:
    from biomed_multi_alignment import Mammal
    model = Mammal.from_pretrained(settings.dti_checkpoint)

TODO: envolver la carga y anadir predict(smiles, protein_seq) -> binding_affinity.
"""


def load_model():
    raise NotImplementedError


def predict_affinity(smiles: str, protein_sequence: str) -> float:
    raise NotImplementedError
PY

cat > app/foundation/llm_client.py << 'PY'
"""Cliente LLM compartido por CAG, RAG y el agente (Fases 4-6).

TODO: envolver el cliente de Anthropic en una funcion comun, para no repetir
la config de API key en cada modulo de generation/.
"""


def get_llm_client():
    raise NotImplementedError
PY

# ---------- generation/cag (Fase 4) ----------
touch app/generation/__init__.py
touch app/generation/cag/__init__.py

cat > app/generation/cag/static_context.py << 'PY'
"""Fase 4 - Prototipo CAG: LLM con contexto fijo (ficha del patogeno diana,
mecanismos de resistencia conocidos), SIN retrieval ni modelo entrenado.

Documenta en el README donde se rompe este enfoque (no escala a mas
patogenos, no puede citar evidencia real mas alla de lo que esta a mano) -
esa limitacion es la que justifica pasar a RAG en la Fase 5.
"""


def answer_with_static_context(question: str) -> str:
    raise NotImplementedError
PY

# ---------- generation/rag (Fase 5) ----------
touch app/generation/rag/__init__.py

cat > app/generation/rag/chunking.py << 'PY'
"""Fase 5 - Trocea los documentos reales (ChEMBL/CO-ADD/literatura) en chunks
indexables.

TODO: definir estrategia de chunking (tamano, overlap) y justificarla en el README.
"""


def chunk_documents(documents: list[str]) -> list[str]:
    raise NotImplementedError
PY

cat > app/generation/rag/embedding.py << 'PY'
"""Fase 5 - Genera embeddings de los chunks para indexarlos.

TODO: elegir modelo de embeddings y documentar por que.
"""


def embed_chunks(chunks: list[str]):
    raise NotImplementedError
PY

cat > app/generation/rag/store.py << 'PY'
"""Fase 5 - Indice vectorial local (p.ej. Chroma) donde se guardan los chunks
embebidos.

TODO: crear/persistir la coleccion y exponer una funcion de busqueda.
"""


def get_vector_store():
    raise NotImplementedError
PY

cat > app/generation/rag/retrieval.py << 'PY'
"""Fase 5 - Recupera los chunks mas relevantes para una consulta y compone
la respuesta citando evidencia real.

TODO: implementar retrieval + prompt de generacion aumentada.
"""


def answer_with_retrieval(question: str) -> str:
    raise NotImplementedError
PY

# ---------- generation/agentic (Fase 6) ----------
touch app/generation/agentic/__init__.py

cat > app/generation/agentic/tools.py << 'PY'
"""Fase 6 - Herramientas que el agente puede invocar: retrieval (RAG) y
prediccion de afinidad (modelo DTI fine-tuneado).

TODO: envolver answer_with_retrieval y predict_affinity como tools con schema.
"""
PY

cat > app/generation/agentic/agent.py << 'PY'
"""Fase 6 - Orquesta el agente: recibe una consulta de reposicionamiento
("que farmacos ya aprobados podrian funcionar contra este patogeno?"),
decide que herramientas usar (RAG y/o modelo DTI) y compone la respuesta
final con evidencia y prediccion de afinidad.

TODO: implementar el bucle del agente (puede ser tool-calling simple con el
SDK de Anthropic).
"""


def run_agent(query: str) -> str:
    raise NotImplementedError
PY

# ---------- training (Fase 3) ----------
touch training/__init__.py

cat > training/lora_finetune.py << 'PY'
"""Fase 3 - Fine-tune ligero con LoRA del checkpoint DTI sobre el dataset
curado de dianas bacterianas (data/processed/).

TODO: implementar el bucle de entrenamiento LoRA sobre app.foundation.dti_model.
"""


def run_lora_finetune():
    raise NotImplementedError
PY

# ---------- evals (Fase 7) ----------
touch evals/__init__.py

cat > evals/metrics.py << 'PY'
"""Fase 7 - Metricas objetivas: RMSE/correlacion del modelo en un holdout
real, calidad del retrieval, verificacion de que el agente no inventa cifras.

TODO: implementar cada metrica.
"""
PY

cat > evals/run.py << 'PY'
"""Fase 7 - Orquesta la evaluacion completa (modelo + retrieval + agente) y
vuelca los resultados a evals/results.json para citarlos en el README.

TODO.
"""

if __name__ == "__main__":
    raise NotImplementedError
PY

# ---------- scripts ----------
cat > scripts/smoke_test.py << 'PY'
"""Prueba rapida end-to-end: carga el modelo, corre una prediccion de
ejemplo y confirma que el pipeline no esta roto. Pensado para correr en
segundos, no para evaluar calidad (eso es evals/).
"""

if __name__ == "__main__":
    raise NotImplementedError
PY

# ---------- tests ----------
touch tests/__init__.py

# ---------- streamlit app (Fase 8) ----------
cat > streamlit_app.py << 'PY'
"""Fase 8 - App publica ligera: interfaz minima sobre app.generation.agentic.agent.

TODO: input de consulta, salida con evidencia citada y afinidad predicha.
"""

import streamlit as st

st.title("EskapeGuard - reposicionamiento de farmacos frente a AMR")
st.write("TODO: conectar con app.generation.agentic.agent.run_agent")
PY

# ---------- config, docs y raiz ----------
cat > pyproject.toml << 'TOML'
[project]
name = "eskapeguard"
version = "0.1.0"
description = "Prediccion y reposicionamiento de farmacos frente a patogenos ESKAPE (AMR)"
requires-python = ">=3.10"
dependencies = [
    "torch>=2.2",
    "transformers>=4.40",
    "biomed-multi-alignment",
    "peft>=0.10",
    "pandas>=2.2",
    "rdkit>=2023.9.1",
    "chromadb>=0.5",
    "anthropic>=0.40",
    "python-dotenv>=1.0",
    "pydantic-settings>=2.0",
    "streamlit>=1.40",
    "pypdf>=4.0",
    "scipy>=1.13",
    "scikit-learn>=1.4",
]
TOML

cat > .env.example << 'ENV'
ANTHROPIC_API_KEY=
ENV

cat > .gitignore << 'GIT'
__pycache__/
*.pyc
.venv/
data/raw/*
!data/raw/.gitkeep
data/processed/*
!data/processed/.gitkeep
.env
*.chroma/
.DS_Store
GIT

cat > docs/decisions.md << 'MD'
# Registro de decisiones tecnicas

Cada decision relevante del proyecto, en una linea: que se decidio, por que,
que alternativa se descarto. Esto es lo que separa un proyecto que aprueba de
uno que destaca - las decisiones deben quedar justificadas, no asumidas.

## Fase 1 - Datos
-

## Fase 4 - CAG
-

## Fase 5 - RAG
-

## Fase 6 - Agente
-
MD

cat > README.md << 'MD'
## Indice

0. [Ficha del proyecto](#0-ficha-del-proyecto)
1. [Descripcion general del producto](#1-descripcion-general-del-producto)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Modelo de datos](#3-modelo-de-datos)
4. [Especificacion de la API](#4-especificacion-de-la-api)
5. [Historias de usuario](#5-historias-de-usuario)
6. [Tickets de trabajo](#6-tickets-de-trabajo)
7. [Pull requests](#7-pull-requests)

---

## 0. Ficha del proyecto

### 0.1. Tu nombre completo:


### 0.2. Nombre del proyecto:
EskapeGuard

### 0.3. Descripcion breve del proyecto:
TODO - 1-2 frases: sistema de prediccion y reposicionamiento de farmacos
frente a resistencia antimicrobiana (AMR), centrado en patogenos ESKAPE.

### 0.4. URL del proyecto:


### 0.5. URL o archivo comprimido del repositorio


---

## 1. Descripcion general del producto

### 1.1. Objetivo:
TODO - que es AMR, por que importa, que patogeno(s) elegiste y por que, que
predice el sistema (afinidad de union molecular) y que NO predice (eficacia
clinica). Para quien es util.

### 1.2. Caracteristicas y funcionalidades principales:
TODO - prediccion de afinidad DTI, recuperacion de evidencia real (RAG),
agente de reposicionamiento, evaluacion objetiva.

### 1.3. Diseno y experiencia de usuario:
TODO - capturas o video de la demo (streamlit_app.py).

### 1.4. Instrucciones de instalacion:
```bash
uv sync
cp .env.example .env  # rellena tu ANTHROPIC_API_KEY
uv run python scripts/smoke_test.py
uv run streamlit run streamlit_app.py
```

---

## 2. Arquitectura del Sistema

### 2.1. Diagrama de arquitectura:
TODO - diagrama de CAG -> RAG -> agente -> evaluacion -> despliegue.

### 2.2. Descripcion de componentes principales:

**CAG** - TODO: que hace, donde se rompe, por que eso justifica pasar a RAG.

**RAG** - TODO: fuentes indexadas (ChEMBL/CO-ADD/literatura), estrategia de
chunking, como cita evidencia.

**Agente** - TODO: que herramientas usa (RAG + modelo DTI), caso de estudio
de reposicionamiento.

**Evaluacion** - TODO: metricas (RMSE/correlacion, calidad de retrieval,
verificacion anti-alucinacion).

**Modelo DTI** - TODO: checkpoint base, fine-tune LoRA.

### 2.3. Descripcion de alto nivel del proyecto y estructura de ficheros
TODO - pega el arbol de app/, training/, evals/ (ver CLAUDE.md) y explica el
porque de ingestion -> foundation -> generation/{cag,rag,agentic}.

### 2.4. Infraestructura y despliegue
TODO - como se despliega la demo (Streamlit Cloud / Hugging Face Spaces /
local + video).

### 2.5. Seguridad
TODO - manejo de la API key (variables de entorno, nunca hardcodeada),
validacion de inputs del agente.

### 2.6. Tests
TODO - que cubren los tests en tests/.

---

## 3. Modelo de Datos

> Adaptado: en vez de un esquema de base de datos relacional, documenta el
> esquema del dataset curado.

### 3.1. Diagrama del modelo de datos:
TODO - esquema del dataset curado (molecula, proteina diana, afinidad,
fuente, positivo/negativo).

### 3.2. Descripcion de entidades principales:
TODO - campos, tipos, procedencia (ChEMBL vs CO-ADD), por que los negativos
reales de CO-ADD hacen la evaluacion mas honesta.

---

## 4. Especificacion de la API
TODO - si expones el agente via API (opcional si solo usas Streamlit),
documenta 1-3 endpoints en formato OpenAPI.

---

## 5. Historias de Usuario

> Ejemplo de historia adaptada al dominio: "Como investigador de AMR quiero
> introducir un patogeno y obtener farmacos ya aprobados candidatos a
> reposicionamiento, con evidencia citada, para priorizar que probar en el
> laboratorio."

**Historia de Usuario 1**

**Historia de Usuario 2**

**Historia de Usuario 3**

---

## 6. Tickets de Trabajo

> Adapta backend/frontend/BBDD a tu pipeline: p.ej. uno de ingesta+modelo,
> uno de RAG+agente, uno de evaluacion+demo.

**Ticket 1**

**Ticket 2**

**Ticket 3**

---

## 7. Pull Requests

**Pull Request 1**

**Pull Request 2**

**Pull Request 3**
MD

cat > prompts.md << 'MD'
> Prompts principales usados con asistentes de codigo durante el desarrollo
> (maximo 3 por seccion) - creacion inicial, correcciones o funcionalidades
> relevantes. Puedes anadir el link a la conversacion completa de Claude.ai
> o Claude Code si lo prefieres a pegar el prompt entero.

## Indice

1. [Descripcion general del producto](#1-descripcion-general-del-producto)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Modelo de datos](#3-modelo-de-datos)
4. [Especificacion de la API](#4-especificacion-de-la-api)
5. [Historias de usuario](#5-historias-de-usuario)
6. [Tickets de trabajo](#6-tickets-de-trabajo)
7. [Pull requests](#7-pull-requests)

---

## 1. Descripcion general del producto

**Prompt 1:**

**Prompt 2:**

**Prompt 3:**

---

## 2. Arquitectura del Sistema

### 2.1. Diagrama de arquitectura:

**Prompt 1:**

### 2.2. Descripcion de componentes principales:

**Prompt 1:**

### 2.3. Descripcion de alto nivel del proyecto y estructura de ficheros

**Prompt 1:**

### 2.4. Infraestructura y despliegue

**Prompt 1:**

### 2.5. Seguridad

**Prompt 1:**

### 2.6. Tests

**Prompt 1:**

---

## 3. Modelo de Datos

**Prompt 1:**

---

## 4. Especificacion de la API

**Prompt 1:**

---

## 5. Historias de Usuario

**Prompt 1:**

---

## 6. Tickets de Trabajo

**Prompt 1:**

---

## 7. Pull Requests

**Prompt 1:**
MD

echo "Listo. Estructura creada."
echo "Revisa app/config.py y ajusta 'pathogens' a tu alcance real."
