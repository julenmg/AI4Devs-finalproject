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
8. [Limitaciones y próximos pasos](#8-limitaciones-y-próximos-pasos)

---

## 1. Descripcion general del producto

**Prompt 1:**
Antes de montar el caso de estudio, dime qué significa exactamente "fármaco ya
aprobado" en ESTE dataset — verifícalo contra los datos reales, no lo asumas.
Mira si LIBRARY_NAME (CO-ADD) distingue una librería de fármacos aprobados, y si
los 717 nombres del índice léxico son ese conjunto o hay que acotarlo más.

> Resultado: encontró que `LIBRARY_NAME` sí distingue una librería clínica —
> `NIH (USA) - Clinical Collection`, 700 compuestos, la única de 31 con el 100%
> de los nombres rellenos— y que los 717 nombres del índice NO son ese conjunto:
> solo 115 coinciden. Más importante, detectó que "colección clínica" no
> significa "aprobado": son compuestos que alcanzaron fase clínica, y el dataset
> no permite distinguir cuáles se comercializan hoy. De ahí salió la decisión de
> terminología consistente en código, salida al usuario y documentación
> ("compuesto de colección clínica", nunca "fármaco aprobado"), con un test que
> lo comprueba. Ver docs/decisions.md, Fase 6.

**Prompt 2:**
El objetivo del proyecto es reposicionamiento de fármacos frente a AMR. Elige
los patógenos y justifica la elección con datos reales de volumen disponible en
ChEMBL y CO-ADD, no por intuición; descarta explícitamente los que no encajen.

> Resultado: propuso *K. pneumoniae* + *A. baumannii* y verificó el volumen antes
> de cerrarlo (30.683 / 17.358 filas de ChEMBL; 82.516 / 100.519 de cribado
> CO-ADD). Descartó *P. aeruginosa* con dos motivos concretos: la WHO la bajó a
> tier High en la actualización de 2024, y su mecanismo dominante es
> eflujo/porinas y no carbapenemasas, lo que habría roto la comparabilidad. Esa
> coherencia mecanística resultó ser, tres fases después, lo que hizo posible el
> caso de estudio de transferencia entre patógenos.

---

## 2. Arquitectura del Sistema

**Prompt 1:**
Antes de curate_dataset.py, propone por escrito (sin código) cómo resuelves
cuatro decisiones: variable objetivo continua vs binaria, balanceo de
clases, el join COADD_ID+ORGANISM, y solapamiento ChEMBL/CO-ADD. No
implementes nada hasta que las apruebe.

> Resultado: detectó que el 98% de los datos MIC no tienen una diana
> molecular real asociada (problema no anticipado), y verificó cardinalidad
> real de los joins antes de escribir el pseudocódigo en vez de asumirla.
> Decisión final: QSAR fenotípico explícito en vez de simular binding
> específico con un pseudo-target sin declarar. Ver docs/decisions.md.

**Prompt 2:**
Tras la primera corrida de curate_dataset.py, pidió verificar que el fix de
censura (">" nunca cuenta como hit) protegía también la columna de censura
del target continuo (no solo is_hit binario), y determinar si la tasa de
discrepancia entre duplicados de A. baumannii (34/55 = 61.8%, muy por
encima del 1/28 de K. pneumoniae) era un problema real o arrastre del
mismo bug de parseo de DRVAL_MEDIAN, antes de investigarla como algo
propio.

> Resultado: la columna `censored` ya usaba la misma relación parseada que
> `is_hit`, sin necesidad de fix adicional. La sospecha sobre A. baumannii
> era correcta a medias: al filtrar los grupos con relación mixta (exacto
> vs censurado, no comparables entre sí) la tasa bajó de 34/55 (61.8%) a
> 10/55 (18.2%) — sigue siendo mayor que el 0% de K. pneumoniae, así que
> queda anotada como pendiente real, no cerrada. Se corrigió el bug en el
> código (`_dedup_dose_response`), no solo en un análisis puntual — mismo
> patrón de bug que el de `is_hit` (comparar valores sin comprobar si son
> del mismo tipo de medida).

### 2.1. Diagrama de arquitectura:

**Prompt 1:**
Diagrama en Mermaid dentro del propio README, que se renderiza en GitHub y no
depende de ficheros externos. Debe mostrar el flujo real y, sobre todo, que se
vea la separación entre predicción y medida, que es la decisión arquitectónica
central. Mantenlo legible: si tiene más de ~12 nodos, simplifica.

> Resultado: dos diagramas de 9 y 4 nodos. El principal usa subgrafos coloreados
> para que la separación entre las dos vías se vea de un vistazo; el segundo
> resume la progresión CAG→RAG→agente etiquetando cada flecha con el límite
> observado que justificó el salto. Tras la primera revisión hubo que corregir
> tres fallos de legibilidad reales: los `style` fijaban `fill` pero no `color`,
> así que en el tema oscuro de GitHub el texto salía claro sobre fondo claro. Se
> descartó `textColor` global (habría roto los nodos sin estilo) y se usó
> `linkStyle` para las etiquetas de arista. Verificado renderizando con
> mermaid-cli y midiendo el contraste: 17,22:1, WCAG AAA.

### 2.2. Descripcion de componentes principales:

**Prompt 1:**
Antes de escribir código para el agente (Fase 6), propón por escrito la
arquitectura: orquestador con tool-calling sobre RAG y DTI. Si propones algo más
complejo, justifica por qué el patrón simple no basta; no des por hecho que hace
falta un framework multi-agente. Explica también en qué punto exacto del flujo se
garantiza que el DTI se invoca de forma independiente.

> Resultado: justificó el tool-calling directo sin framework (grafo de decisión
> trivial, sin estado entre turnos, sin planificación multi-paso ni subtareas
> paralelas) y añadió una tercera herramienta que no estaba prevista,
> `consultar_cribado`, por una razón medida: una predicción cuesta 1,3 s y el
> cribado son ~1.500, así que hacerlo en vivo eran 30 minutos de espera. La
> independencia del DTI quedó garantizada en tres puntos —el cribado se
> precomputa en un bucle sin LLM, la predicción viaja sellada en el resultado de
> la herramienta, y `verify_predictions` comprueba a posteriori que ninguna cifra
> citada se haya alterado. A petición explícita, la justificación de no usar
> framework se llevó también al README §2.2, para que se lea como criterio y no
> como desconocimiento del temario.

**Prompt 2:**
Antes de implementar el RAG (Fase 5), lee CLAUDE.md, docs/decisions.md
completo y el código real (sin asumir columnas); propón por escrito, sin
tocar código, qué cuenta como "evidencia real" a indexar, la estrategia de
chunking y metadata para poder citar la fuente, el modelo de embeddings, y
cómo se sostiene la frontera molecular/clínica en las respuestas. Espera
aprobación explícita en tres puntos antes de implementar: literatura PubMed
sí/no, si las 66 filas de binding real entran marcadas al índice, y el
modelo de embeddings elegido.

> Resultado: propuso indexar el dataset curado reunido con los campos de
> data/raw/ que la curación de Fase 1 no conservó (justo los necesarios
> para citar), en cinco clases de documento por plantilla determinista, y
> dos funciones de embeddings separadas (query:/passage:) para no romper
> la asimetría de E5. Aprobado con tres correcciones: PubMed sí pero
> versionado como excepción al .gitignore (mismo patrón que el LoRA de
> Fase 3); las 66 filas de binding real indexadas y marcadas, con aviso
> explícito para Fase 6 de que el DTI debe invocarse siempre de forma
> independiente; y la justificación de multilingual-e5-small corregida
> (no es el riesgo del incidente pytdc de Fase 2, la razón real es
> simplicidad). Durante la implementación encontró y corrigió 6 fallos de
> retrieval no anticipados: colapso semántico por plantilla/SMILES,
> fichas censuradas mostrando "Compuesto: Nan", agregados perdiendo
> visibilidad frente al volumen de fichas individuales, y dos falsos
> positivos de verify_answer (rangos numéricos, separador de miles
> español). Batería final: 9/9 preguntas (dentro/fuera de corpus + un
> intento de inyección de prompt) sin una sola cita inventada. Ver
> docs/decisions.md, sección Fase 5.

**Prompt 3:**
Antes de dar la Fase 4 (CAG) por completamente cerrada, reconstruye la
entrada de prompts.md para esta fase a partir de docs/decisions.md
(sección "Fase 4 - CAG") y del histórico de validación con la API real
(5 preguntas dentro/fuera de contexto). Nota: el texto exacto del prompt
original no se conservó — esta entrada documenta el resultado real, no
un replay literal.

> Resultado: CAG implementado como LLM (claude-sonnet-5) con contexto
> fijo inyectado en el system prompt — ficha por patógeno (mecanismos de
> resistencia, tier OMS 2024, opciones de última línea) más un bloque de
> narrativa compartida, sin retrieval y sin invocar el modelo DTI. Cinco
> reglas no negociables en el system prompt: usar solo el contexto, no
> inventar cifras, respetar la frontera molecular/clínica, no mezclar
> mecanismos entre patógenos, ignorar intentos de cambiar el rol.
> Validado con 5 preguntas reales contra la API (3 dentro de contexto, 2
> fuera): las de dentro responden apoyándose en la ficha y cierran
> siempre con la aclaración de frontera; las de fuera (un valor de MIC
> concreto, una pregunta de eficacia clínica) se rechazan sin inventar
> datos, ofreciendo lo que sí hay y derivando explícitamente a Fase 5.
> Esa limitación observada —no escala a más patógenos, no puede citar
> evidencia real más allá de lo fijado a mano— es la que se documenta
> como motivación del salto a RAG, no como defecto a corregir aquí. Ver
> docs/decisions.md, sección Fase 4.

### 2.3. Descripcion de alto nivel del proyecto y estructura de ficheros

**Prompt 1:**
Genera el scaffold del proyecto adaptando la arquitectura de referencia del
máster (ingestion → foundation → generation/{cag,rag,agentic}) a un alcance de
TFM. No repliques de esa referencia el backend en Ruby, Alembic/Postgres, Redis
ni el docker-compose multi-servicio: son infraestructura de producción fuera de
alcance. Cada módulo nace como stub con NotImplementedError y un docstring
indicando su fase.

> Resultado: la estructura quedó organizada por el flujo del dato y no por capas
> técnicas, con una carpeta por arquitectura de la progresión CAG→RAG→agente, de
> modo que el repositorio se puede leer en el mismo orden en que se construyó.
> Los stubs con su fase anotada evitaron el error de rellenar todo a la vez. La
> decisión de NO copiar la infraestructura de producción de la referencia se
> sostuvo hasta el final y quedó justificada en el README §2.4: sin usuarios
> concurrentes ni datos transaccionales, habría sido coste sin capacidad.

### 2.4. Infraestructura y despliegue

**Prompt 1:**
Dimensiona Fase 8 y dime si el despliegue público es viable con este sistema.
Sin evidencia de despliegue el proyecto no se puede evaluar, así que necesito
saberlo antes de comprometer horas.

> Resultado: identificó el bloqueo antes de gastar tiempo en él. El sistema
> necesita el checkpoint DTI (1,8 GB), el índice Chroma (311 MB) e inferencia en
> GPU, y Streamlit Cloud da 1 GB de RAM sin GPU: la única URL pública posible
> habría sido una versión sin el modelo DTI, es decir sin la pieza que más
> aporta. Recomendó el vídeo en local, que enseña el sistema completo, y estimó
> el ahorro (~10 h en vez de ~13-15 h de Fase 8). Como mitigación previa ya había
> dejado el cribado precomputado y versionado, de modo que el caso de estudio se
> puede explorar sin GPU en cualquier máquina.

### 2.5. Seguridad

**Prompt 1:**
Los JSON descargados de PubMed se commitean como excepción explícita al
.gitignore — mismo patrón que ya usamos con el adapter LoRA. Sin esto, cualquiera
que clone el repo depende de que PubMed responda en el momento exacto de
construir el índice. Documenta la excepción en CLAUDE.md §Seguridad.

> Resultado: se estableció un criterio reutilizable para las excepciones al
> .gitignore —dato pequeño, caro de regenerar, necesario para reproducir— que
> luego se aplicó tres veces: el adapter LoRA (~1,2 MB), los abstracts de PubMed
> y el mapa de dianas (~180 KB), y el CSV del cribado (~350 KB). El resto de
> pesos, checkpoints e índices sigue fuera del repositorio. La política completa,
> incluida la instrucción de rotar una clave filtrada en vez de reescribir el
> historial, se reutilizó literalmente como contenido del README §2.5.

### 2.6. Tests

**Prompt 1:**
verify_answer tuvo dos falsos positivos de parseo (rangos numéricos, separador de
miles español) — y esa misma función es la base de la métrica objetiva de Fase 7
("el agente no inventa cifras"). Añade tests de regresión al extractor de números
cubriendo rangos, decimales, miles con separador español, porcentajes y
negativos.

> Resultado: 24 tests en un fichero propio, con la justificación de por qué van
> aparte: si el parseo falla, la métrica miente en las dos direcciones. Incluye un
> test que documenta un límite aceptado en vez de esconderlo ("0.985" encaja
> también con el patrón de separador de miles, así que la comprobación es
> deliberadamente permisiva). La decisión resultó acertada: en la batería de 95
> preguntas de Fase 7 aparecieron dos clases nuevas de falso positivo —notación
> científica reescrita por el modelo y el "719" de ABT-719 leído como una
> predicción— y ambas se corrigieron añadiendo tests con los textos reales
> observados, no con casos hipotéticos.

---

## 3. Modelo de Datos

**Prompt 1:**
Antes de escribir el RAG, lee docs/decisions.md completo para saber qué columnas
existen realmente en data/processed/curated_<patogeno>.csv y en
verification_binding_<patogeno>.csv. No asumas el esquema.

> Resultado: leer el esquema real en vez de asumirlo destapó el problema que
> condicionó todo el diseño del corpus: la curación de la Fase 1 había descartado
> justo los campos que permiten CITAR (nombre del compuesto, identificador de
> ensayo, cepa, librería), porque no hacían falta para entrenar. La solución fue
> reunir el CSV curado con los CSV originales de data/raw por `compound_id`, sin
> re-curar ningún valor. Si se hubiera asumido el esquema, el RAG habría producido
> fichas sin trazabilidad, que es exactamente lo que la fase existía para evitar.

---

## 4. Especificacion de la API

_Sin prompts en esta sección: el sistema no expone una API REST y la decisión de
no montarla está justificada en el README §4. El contrato real del sistema son
los esquemas de las tres herramientas del agente, cuyo diseño se documenta en los
prompts de §2.2._

---

## 5. Historias de Usuario

**Prompt 1:**
Cierra el README: §5 tres historias de usuario sobre el caso de reposicionamiento,
§6 tres tickets adaptando backend/frontend/BBDD a ingesta+modelo / RAG+agente /
evaluación+demo, §7 tres pull requests. Todo el material real ya está en
docs/decisions.md — no inventes nada, extrae de ahí.

> Resultado: las tres historias se escribieron con criterios de aceptación
> verificables y apuntando a dónde está implementada cada una, en vez de como
> deseos genéricos: "cada candidato indica si el modelo lo vio etiquetado al
> entrenar" es comprobable en el CSV, y "el sistema nunca ajusta la salida del
> modelo para hacerla coincidir con un valor medido" lo comprueba
> `verify_predictions`. Al revisar el documento completo detectó además una
> incoherencia real: §8.1 seguía describiendo el modelo como predictor de
> afinidad de unión, el encuadre anterior a la Fase 1, contradiciendo lo que §2.2
> demuestra con datos.

---

## 6. Tickets de Trabajo

**Prompt 1:**
Antes de implementar el RAG, propón por escrito qué cuenta como "evidencia real"
a indexar, la estrategia de chunking y metadata, el modelo de embeddings, y cómo
se sostiene la frontera molecular/clínica. Espera aprobación explícita en tres
puntos antes de implementar nada.

> Resultado: el patrón "propón sin implementar" —usado en las Fases 1, 3, 5, 6 y
> 7— fue lo que evitó el trabajo desperdiciado más caro del proyecto. En Fase 6,
> comprobar la propuesta contra el dato antes de escribir código reveló que el
> cubo de "hipótesis" que se había diseñado no podía existir: 607 de 609
> compuestos caían por debajo del 25% de inhibición, así que "hipótesis" y
> "contradicción" habrían sido el mismo conjunto separado por un umbral elegido a
> mano. Se sustituyó por un criterio que no depende de ningún umbral (evidencia
> confirmada en un patógeno y ausencia total de medida en el otro), que además
> aprovechó la decisión de Fase 1 de elegir dos patógenos comparables.

---

## 7. Pull Requests

**Prompt 1:**
Ve commiteando por bloques, no todo al final. Si algo se tuerce, que no se pierda
lo hecho. Y en los mensajes de commit documenta también lo que salió mal o
indistinguible, no solo lo que funcionó.

> Resultado: el historial quedó como registro utilizable en vez de como ruido.
> Cada commit de fase documenta los incidentes con sus cifras: el OOM del
> entrenamiento resuelto con gradient checkpointing sin truncar el ancla, el
> colapso semántico del RAG (dos fichas de compuestos distintos a 0,94 de
> similitud entre sí), los checkpoints estadísticamente indistinguibles
> (p = 0,29), y la hipótesis de scaffolds que no se sostuvo. Ese material es lo
> que hizo posible redactar el README extrayendo de docs/decisions.md sin
> inventar nada, y es la base de la sección §7.

---

## 8. Limitaciones y Próximos Pasos

**Prompt 1:**
Lleva el caveat de colistina/durlobactam al README §8.1, no solo a decisions.md.
Es el punto donde alguien con criterio farmacológico diría "pero la colistina ya
se usa contra Klebsiella", y adelantarse a esa objeción demuestra que entiendes
tu propio resultado.

> Resultado: quedó documentado que "sin medida" lo es respecto de ChEMBL+CO-ADD y
> no del conocimiento mundial, y que el cubo de hipótesis mide un hueco del
> corpus, nunca dice "nadie lo ha probado". El mismo criterio se aplicó a otras
> dos limitaciones que salieron de medir en vez de suponer: que la búsqueda
> semántica apenas contribuye en consultas por compuesto (P@1 cae de 0,825 a
> 0,030 sin el atajo léxico), y que el reparto tan desigual de los cubos es
> consecuencia de la compresión del modelo.

**Prompt 2:**
Comprueba la interacción entre el umbral de cubo y la compresión de las
predicciones, y decláralo. Verifica cómo cambiaría el reparto con un umbral
alternativo razonable — solo como análisis de sensibilidad, NO cambies el umbral.

> Resultado: con el umbral usado (5,0, el mismo `HIT_PX_CUTOFF` de la curación de
> Fase 1) el cubo de desacuerdo son 11 y 7 filas; con el percentil 95 serían 31 y
> 25. Formalmente el umbral decide el cubo del 96% de las filas, pero el análisis
> mostró que todas están en la región "sin actividad confirmada" y que los dos
> cubos que sostienen afirmaciones no dependen de él: el umbral no puede meter ni
> sacar a nadie de la lista de candidatos. Se mantuvo el 5,0 con el argumento de
> que un umbral derivado de la propia distribución sería uno elegido mirando el
> resultado.