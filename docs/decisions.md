# Registro de decisiones tecnicas

Cada decision relevante del proyecto, en una linea: que se decidio, por que,
que alternativa se descarto. Esto es lo que separa un proyecto que aprueba de
uno que destaca - las decisiones deben quedar justificadas, no asumidas.

## Fase 1 - Datos
- ChEMBL: se usa la API REST publica (`/chembl/api/data/activity`, sin API
  key) filtrando por `target_organism` + `standard_type__in` (MIC, IC50,
  EC50, GI50, Ki, Kd). Alternativa descartada: `chembl_webresource_client`
  (anade una dependencia extra solo para envolver la misma API REST que ya
  consumimos directo con `requests`).
- CO-ADD: se descarga el bulk publico "Complete Data Set" (release r03,
  02-2020, la mas reciente en la web a fecha de escritura) desde
  `db.co-add.org/downloads` en vez de scrapear la UI de busqueda. Trae dos
  CSV: Inhibition (cribado primario, incluye negativos reales) y Dose
  Response (MIC confirmatorio de los activos). El fichero de Inhibition
  (~160 MB) se procesa en chunks para no cargarlo entero en memoria.
- TLS de db.co-add.org: el servidor no envia su certificado intermedio
  (chain incompleta), asi que la verificacion falla con las CA por defecto.
  Se completa la cadena descargando el intermedio real via la URL AIA del
  propio certificado en vez de desactivar la verificacion (`verify=False`).
- Alcance final: **Klebsiella pneumoniae + Acinetobacter baumannii**
  (`app/config.py`). Ambos figuran como tier "Critical" en la WHO Bacterial
  Priority Pathogens List 2024, y comparten familia mecanistica de
  resistencia (carbapenemasas: KPC/NDM/OXA-48 en K. pneumoniae, OXA-23/24/58
  en A. baumannii) — esto da comparabilidad real al caso de reposicionamiento
  en vez de mezclar mecanismos no relacionados. Alternativa descartada:
  Pseudomonas aeruginosa (mismo tier, pero mecanismo dominante es eflujo/porinas,
  no carbapenemasas — rompe la comparabilidad buscada).
  Volumen real descargado (ver ambos loaders):
  | Fuente | K. pneumoniae | A. baumannii |
  |---|---|---|
  | ChEMBL (bioactividades) | 30 683 | 17 358 |
  | CO-ADD inhibition (cribado primario) | 82 516 | 100 519 |
  | CO-ADD dose response (MIC confirmatorio) | 4 631 | 4 904 |

  A. baumannii tiene menos ChEMBL (57% del volumen de K. pneumoniae) pero
  *mas* CO-ADD en ambos ficheros — ninguna fuente cae por debajo de una
  decima parte de la otra, así que no hay razón para reducir el alcance a
  un solo patógeno.
- CO-ADD positivos/negativos: a concentracion unica (INHIB_AVE, fichero
  Inhibition) los "positivos" son un porcentaje minúsculo del total incluso
  con un umbral laxo (>=50% inhibicion): 0.09% en K. pneumoniae, 1.48% en
  A. baumannii; con el umbral de hit real de CO-ADD (>=80%) baja a 0.05% y
  0.29% respectivamente. Esto es esperado en cribado primario (la mayoría de
  una quimioteca no tiene actividad) y es precisamente lo que hace útil a
  CO-ADD frente a datasets solo-positivos — pero implica que
  `curate_dataset.py` tendrá que decidir cómo balancear clases (undersampling
  de negativos, class weights, etc.) en vez de asumir un dataset equilibrado.
- Nota para Fase 3: el fichero Dose Response trae bastantes mas filas por
  patogeno (4 631 / 4 904) que "hits" al 80% en Inhibition (41 / 289) — el
  seguimiento dose-response no se restringe a los hits del organismo
  concreto, así que `curate_dataset.py` no puede asumir que dose-response
  implica positivo en single-concentration para ese mismo organismo; hay que
  cruzar por COADD_ID + ORGANISM explícitamente.

### `curate_dataset.py` — decisiones y resultados reales

- **Encuadre del fine-tune: QSAR de potencia fenotípica (SMILES -> pMIC),
  no binding fármaco-diana.** ~98% de las filas de ChEMBL son MIC/IC50/EC50
  de célula completa (target = el organismo, no una proteína concreta);
  tratarlas como afinidad de unión real habría cruzado la frontera que el
  propio CLAUDE.md pide vigilar. Alternativa descartada: fijar una
  proteína-diana "pseudo-target" por patógeno para poder reusar el schema
  (drug, target)->pKd del checkpoint tal cual — se descarta porque
  disfrazaría el 98% del dato de binding específico cuando no lo es, y con
  solo ~66 filas de Ki/Kd reales tampoco aportaba sustancia real. Si la
  interfaz del modelo (Fase 2/3) exige técnicamente un input de "target",
  se usará un valor fijo por patógeno documentado explícitamente como
  requisito de arquitectura, nunca como afirmación de unión real.
- **Verificación de binding real apartada para Fase 7:** los Ki/Kd
  auténticos (contra una diana molecular concreta, no el organismo) se
  guardan sin mezclar en `verification_binding_<patogeno>.csv` — **53
  filas en K. pneumoniae (36 Kd + 18 Ki) + 13 en A. baumannii (Ki) = 66
  filas reales**. (Corrección: en la propuesta previa dije "49" — sumé mal,
  me dejé el Ki=18 de Klebsiella fuera. El código (`BINDING_STANDARD_TYPES
  = {"Ki", "Kd"}`) siempre fue correcto, el error era solo en mi cuenta
  verbal.)
- **Descubrimiento durante la implementación — CO-ADD Dose Response no es
  mayoritariamente "hits confirmados":** `DRVAL_MEDIAN` embebe el operador
  relacional en el propio string (p.ej. `">10"`), a diferencia de ChEMBL que
  lo trae en columna aparte. Al parsearlo: **4 572 de 4 631 filas en K.
  pneumoniae (98.7%) son `">"`-censuradas** (el compuesto no hizo efecto ni
  siquiera a la concentración más alta probada contra ESE organismo) — solo
  57 filas tienen un MIC realmente determinado. El mismo problema existía
  latente en el lado de ChEMBL (`standard_relation` `>`/`>=` en ~29% de las
  filas de K. pneumoniae) y no estaba filtrado en el borrador inicial de
  `is_hit`. Corregido: `is_hit=True` requiere un valor de potencia real
  *no* censurado al alza (`_resolve_is_hit`); una fila `">X"` nunca cuenta
  como hit aunque el pX calculado a partir de X supere el umbral.
- **Umbral de hit:** `pX >= 5.0` (~10 µM o más potente) cuando hay valor de
  potencia real medido; `INHIB_AVE >= 80` (umbral propio de CO-ADD) cuando
  solo existe el punto único de cribado primario.
- **Regla de "negativo trivial" (para undersampling), aplicada tal cual se
  aprobó:** fila de `coadd_inhibition` sin seguimiento en dose-response
  para ese organismo, con `INHIB_AVE < 25` **y** similitud de Tanimoto
  (Morgan/ECFP4, radio 2, 2048 bits) `< 0.4` frente a todo el set de
  anclaje (filas `is_hit=True` de ChEMBL funcional + CO-ADD). Tope:
  máximo 20 negativos triviales por cada fila no-trivial, muestreo con
  semilla fija (42).
- **Resultado real del undersampling — no redujo nada en ningún patógeno:**

  | | K. pneumoniae | A. baumannii |
  |---|---|---|
  | Candidatos a trivial | 74 230 | 84 732 |
  | No-triviales | 8 280 | 15 781 |
  | Tope (20× no-triviales) | 165 600 | 315 620 |
  | Filas CO-ADD antes -> después | 82 510 -> 82 510 | 100 513 -> 100 513 |
  | Ratio positivos/total antes -> después | 0.0267% -> 0.0267% | 0.0418% -> 0.0418% |

  El tope (20×) quedó muy por encima del volumen real de candidatos
  triviales en ambos patógenos, así que el undersampling no se disparó: no
  se descartó ni una fila. Esto significa que **el desequilibrio real
  (~1:3750 K. pneumoniae, ~1:2390 A. baumannii) queda íntegro en
  `curated_<patogeno>.csv`** — el balanceo de clases recae por completo en
  los class weights de Fase 3 (entrenamiento), no en la curación. Si se
  quiere reducir el tamaño del fichero curado de verdad, hay que bajar el
  ratio del tope (p.ej. 5× en vez de 20×) — no se ha hecho porque no se
  pidió explícitamente y no descartar datos reales por defecto es la
  opción más conservadora.
- **Discrepancias ChEMBL vs CO-ADD (mismo InChIKey, mismo organismo, ambos
  valores sin censurar) — no se excluye nada automáticamente, se loggea:**
  K. pneumoniae 243 pares solapados, 10 (4.1%) superan 2 log de diferencia;
  A. baumannii 77 pares, 4 (5.2%). Fracción pequeña en ambos casos — no se
  considera necesario revisar/excluir antes de Fase 3, pero queda todo en
  `discrepancies_chembl_coadd_<patogeno>.csv` para auditoría.
- **Duplicados dentro de CO-ADD dose-response** (mismo `COADD_ID` repetido):
  se resuelven quedándose con la fila de `NASSAYS` más alto. **Corrección
  tras revisión (ver más abajo):** el cálculo inicial de "supera tolerancia"
  comparaba `pX` en bruto sin comprobar si las filas del grupo eran
  comparables (exacto "=" vs censurado ">"), lo que inflaba la tasa de
  discrepancia con casos que no lo eran. Corregido en
  `_dedup_dose_response`: `exceeds_tolerance` solo se evalúa cuando todas
  las filas del grupo comparten la misma relación; si no, se marca
  `mixed_relation` (no es una discrepancia real). Cifras corregidas:
  K. pneumoniae 28 grupos (2 mezcla exacto/censurado, **0 discrepancias
  reales**, antes decía 1); A. baumannii 55 grupos (32 mezcla,
  **10 discrepancias reales de 55 = 18.2%**, antes decía 34/55 = 61.8%).
  Reporte completo en `discrepancies_dose_response_duplicates_<patogeno>.csv`.
- **Discrepancia de A. baumannii — investigada y cerrada como limitación
  aceptada (no es problema de calidad):** los 10 grupos genuinos
  (`mixed_relation=False, exceeds_tolerance=True`) son todos ruido normal de
  dilución de MIC. El MIC se determina en diluciones seriadas al doble
  (1, 2, 4, 8, 16, 32 µg/mL), así que los `log_diff` observados son
  exactamente múltiplos de log₁₀(2)=0.301: 0.301 (1 dilución de diferencia),
  0.602 (2), 0.903 (3). Todos son valores `=` en `ug/mL`, mismo compuesto
  retesteado en réplica. El umbral `DUPLICATE_LOG_DIFF_TOLERANCE=0.3` marca
  cualquier diferencia de **una sola dilución**, que en microbiología se
  considera concordante (±1 dilución es el estándar de reproducibilidad de
  MIC). Por tanto no es una discrepancia real de medida; el 0% de
  K. pneumoniae vs 18.2% de A. baumannii solo refleja que A. baumannii tuvo
  más compuestos retesteados a caballo de un límite de dilución, no peor
  calidad de dato. No afecta al dataset curado (la deduplicación se queda con
  la fila de `NASSAYS` más alto, que es la mejor soportada). Nota para
  Fase 3: si en algún momento se quiere que el reporte solo marque
  discrepancias biológicamente relevantes, subir la tolerancia a >0.6 log
  (>1 dilución); se deja en 0.3 a propósito para que el CSV capture también
  el ruido de dilución y quede auditable.
- **Censura en el target continuo (pMIC), no solo en `is_hit`:** la columna
  `censored` de `curated_<patogeno>.csv` usa la misma relación parseada
  (`standard_relation`/`dr_relation`) que la corrección de `is_hit`, así
  que ya reflejaba correctamente cualquier valor no-exacto (no hacía falta
  fix adicional). Fracción con señal cuantitativa real ("=") vs solo
  censura, por patógeno:

  | | K. pneumoniae | A. baumannii |
  |---|---|---|
  | Valor real ("=") | 20 733 (18.34%) | 11 168 (9.48%) |
  | Censurado (">"/"<"/sin dose-response) | 92 325 (81.66%) | 106 685 (90.52%) |

  La gran mayoría del dataset curado es señal censurada (sobre todo
  CO-ADD inhibition-only, que nunca tuvo seguimiento en dose-response) —
  Fase 3 tiene que entrenar con eso en mente (p.ej. pérdida tipo Tobit/censored
  regression, o al menos no tratar el censurado como si fuera exacto).
- **Pendiente para Fase 3 (balanceo de clases):** poner un tope explícito al
  peso máximo de clase en la loss (no dejar que un único positivo mal
  etiquetado domine el gradiente) y validar con una métrica específica
  sobre la clase minoritaria en el hold-out (p.ej. recall/PR-AUC de hits),
  no solo el loss agregado.

## Fase 4 - CAG
-

## Fase 5 - RAG
- Vector store: Chroma (embebido, persistencia local) en vez de Postgres+pgvector.
  El proyecto de referencia usa Postgres porque tiene datos transaccionales
  propios (usuarios, proyectos, presupuestos) que este proyecto no tiene — el
  dataset ChEMBL/CO-ADD es de solo lectura una vez curado, sin necesidad de
  relaciones ni transacciones. Un vector store dedicado evita la complejidad
  de gestionar un servidor de base de datos sin beneficio real a esta escala.
- Sin Redis: no hay concurrencia de múltiples usuarios que justifique caché
  compartida ni colas — la demo sirve una sesión a la vez.

## Fase 6 - Agente
-
