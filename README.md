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

---

## 8. Limitaciones y Próximos Pasos

> Sección explícitamente requerida en el documento oficial del Proyecto
> Final (aparte de arquitectura/componentes) - no está en las secciones
> genericas de AI4Devs-finalproject, pero es parte de lo que se evalua.

### 8.1. Limitaciones conocidas
TODO - ej: alcance a 1-2 patogenos (no los 6 ESKAPE), el modelo predice
afinidad de union molecular, no eficacia clinica; tamano del dataset
curado; cobertura del RAG limitada a las fuentes indexadas.

### 8.2. Proximos pasos
TODO - ej: ampliar a mas patogenos ESKAPE, mejorar el retrieval con
reranking, anadir mas fuentes a la base RAG, evaluacion mas exhaustiva.