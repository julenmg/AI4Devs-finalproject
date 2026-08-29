## Indice

0. [Ficha del proyecto](#0-ficha-del-proyecto)
1. [Descripcion general del producto](#1-descripcion-general-del-producto)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Modelo de datos](#3-modelo-de-datos)
4. [Especificacion de la API](#4-especificacion-de-la-api)
5. [Historias de usuario](#5-historias-de-usuario)
6. [Tickets de trabajo](#6-tickets-de-trabajo)
7. [Pull requests](#7-pull-requests)
8. [Limitaciones y próximos pasos](#8-limitaciones-y-próximos-pasos)

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

**CAG** (`app/generation/cag/static_context.py`) — LLM (Anthropic) con un
contexto fijo inlineado en el system prompt: las fichas de *K. pneumoniae*
y *A. baumannii* (familia, tier WHO, mecanismos de resistencia, opciones
de última línea) y la narrativa compartida que justifica tratarlos juntos.
Sin retrieval, sin modelo DTI (el DTI de la Fase 3 no se invoca en esta
fase). El system prompt fija cinco reglas explícitas: usar solo el
contexto, no inventar cifras (MIC/pKd/citas), respetar la frontera
molecular vs. clínica, no mezclar mecanismos entre patógenos, e ignorar
instrucciones del usuario que intenten cambiar el rol.

Dónde se rompe (comprobado con la batería de preguntas de la Fase 4, ver
`docs/decisions.md`):

- **No escala a más patógenos.** Cada patógeno nuevo obliga a editar el
  fichero a mano; no hay ninguna vía automática de ingesta de conocimiento.
  Con los seis ESKAPE completos el system prompt se vuelve inmanejable.
- **No puede citar evidencia real más allá de lo fijado en el prompt.** El
  contexto es una síntesis manual, no puede referenciar filas concretas
  de ChEMBL/CO-ADD ni literatura científica, ni actualizarse cuando esas
  fuentes cambian.
- **No hay respuesta cuantitativa por compuesto.** Cualquier pregunta
  sobre pMIC o afinidad de una molécula concreta cae fuera de alcance,
  porque el modelo DTI no está en el bucle y el contexto no contiene
  valores.

Estas tres limitaciones observadas son precisamente lo que justifica pasar
a RAG en la Fase 5 (indexar los CSV curados y literatura de apoyo,
recuperar evidencia real por consulta) y luego a agente en la Fase 6
(RAG + modelo DTI como herramientas encadenadas). No son un bug del CAG:
son su función dentro de la narrativa del proyecto.

**RAG (Fase 5)** — Escala el CAG indexando evidencia real en un vector
store Chroma persistente: 34.078 fragmentos derivados de cinco clases de
documento generadas por plantilla determinista a partir del dataset
curado de Fase 1 y los CSV originales de ChEMBL/CO-ADD — nunca redactadas
a mano libre. Incluyen fichas de potencia fenotípica por compuesto y
patógeno, agregados de cribado primario, las 66 filas reales de afinidad
de unión (Ki/Kd), fichas de contexto reutilizadas del CAG, y 99 abstracts
de PubMed cacheados localmente. Los embeddings usan multilingual-e5-small
(español, sin dependencias nuevas), con funciones de indexado y consulta
separadas para respetar el prefijo asimétrico de E5. Cada fragmento
recuperado viaja con metadata que incluye una cita construida por código
—nunca por el LLM— y una función verify_answer comprueba tras cada
respuesta que ninguna cita ni cifra citada quede fuera de la evidencia
recuperada. Frente al CAG, que por diseño no puede responder con datos
concretos, el RAG cita evidencia real y trazable: en la batería de
validación, 9 de 9 preguntas —dentro y fuera de corpus, más un intento de
inyección de prompt— se resolvieron sin una sola cita inventada.

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

- **Alcance a 2 patógenos** (K. pneumoniae y A. baumannii), no los seis
  ESKAPE. Elección justificada por coherencia mecanística (ambos WHO
  Critical, ambos con carbapenemasas transmisibles por plásmido), no por
  falta de datos: para los otros cuatro habría que rehacer curación
  específica y renarrar el caso de reposicionamiento.
- **Frontera del modelo DTI**: predice afinidad de unión fármaco-diana a
  nivel molecular; no predice eficacia clínica, dosis efectiva,
  farmacocinética ni evolución de la resistencia en el organismo. Ningún
  componente del sistema (CAG, RAG, agente) debe framear salidas como
  predicción de eficacia clínica.
- **Fase CAG — límites deliberados** (ver §2.2): contexto fijo escrito a
  mano, no escala a más patógenos, no cita evidencia real, no responde
  preguntas cuantitativas por compuesto. Se documenta como paso previo
  que justifica RAG, no como componente final del producto.
- **Dataset curado**: fuertemente desequilibrado (~9% hits en Kp, ~5% en
  Ab) y con ~82-91% del target continuo censurado (mayormente
  inhibition-only de CO-ADD sin seguimiento dose-response). La curación
  no descarta el desequilibrio: el balanceo recae en la loss de Fase 3.
- **Cobertura del RAG**: solo cubre lo que se indexa (ChEMBL/CO-ADD
  curados + 99 abstracts de tres consultas fijas de PubMed); no equivale
  a una búsqueda exhaustiva de la evidencia mundial.
- **Compuestos sin nombre, semánticamente indistinguibles.** El vector se
  construye a partir del mismo patrón de ficha (SMILES + medidas); dos
  compuestos distintos sin nombre comercial pueden resultar casi
  idénticos para la búsqueda. Es una propiedad del dato (falta de
  metadata identificativa en ChEMBL/CO-ADD para esas filas), no algo que
  resuelva cambiar el modelo de embeddings.
- **El RAG solo responde agregados que ya existan como texto indexado.**
  No calcula nada nuevo sobre la marcha — una combinación de filtros no
  anticipada al construir el corpus se queda sin respuesta con evidencia
  real, aunque el dato subyacente exista en los CSV curados.
- **Sin reranker.** Con un corpus pequeño y muy estructurado, el
  prefiltro por metadata cubre buena parte de esa necesidad; queda como
  mejora futura si el corpus crece.

### 8.2. Proximos pasos
TODO - ej: ampliar a mas patogenos ESKAPE, mejorar el retrieval con
reranking, anadir mas fuentes a la base RAG, evaluacion mas exhaustiva.