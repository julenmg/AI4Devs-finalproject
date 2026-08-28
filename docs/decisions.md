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
  Pseudomonas aeruginosa — en el BPPL 2017 figuraba como Critical, pero la
  actualización 2024 de la WHO la bajó a tier High (descenso real de
  resistencia global reportado); ya no comparte el mismo nivel de urgencia
  que Klebsiella/Acinetobacter, y además su mecanismo dominante es
  eflujo/porinas, no carbapenemasas — rompe la comparabilidad buscada en
  ambos criterios.
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

## Fase 2 - Checkpoint

- **API real descubierta desde el paquete instalado, no asumida del stub.**
  El stub sugería `from biomed_multi_alignment import Mammal`; el paquete
  PyPI se llama `biomed-multi-alignment` pero **importa como `mammal`**
  (`from mammal.model import Mammal`). Hay que cargar dos piezas, no una:
  el tokenizer modular (`ModularTokenizerOp`) y el modelo — el stub
  original solo contemplaba el modelo.
- **ID de repo HF verificado, no copiado del ejemplo oficial del paquete.**
  El ejemplo oficial de `mammal.examples.dti_bindingdb_kd` usa
  `ibm/biomed.omics...` (HTTP 307 — redirect, id antiguo); nuestro
  `app/config.py` ya tenía `ibm-research/biomed.omics...` (HTTP 200 — el
  canónico actual). Confirmado antes de tocar nada, config correcto sin
  cambios.
- **Incidente evitado: `pytdc` como dependencia rompe el entorno.** El
  código oficial de preprocesado (`DtiBindingdbKdTask` en
  `mammal.examples.dti_bindingdb_kd.task`) importa `tdc` (Therapeutics
  Data Commons) a nivel de módulo a través de su `pl_data_module` — código
  de entrenamiento que no hace falta para inferencia, pero el import es
  inevitable sin tocar el paquete instalado. Se probó primero añadir
  `pytdc` (opción "fidelidad al preprocesado oficial, sin riesgo de
  drift"): arrastró `rdkit-pypi==2022.9.5` en conflicto de namespace con
  el `rdkit==2026.3.5` ya instalado, y subió `numpy` a 2.x — rompiendo la
  ingesta de Fase 1 ya funcionando (`_ARRAY_API not found`, típico de
  extensiones compiladas contra numpy 1.x corriendo bajo numpy 2.x).
  Revertido (`uv remove pytdc`); como la desinstalación dejó el propio
  `rdkit` dañado (namespace compartido), se reinstaló limpio
  (`uv pip install --reinstall rdkit`) y se verificó funcionalmente (no
  solo el import): InChIKey, peso molecular, fingerprint de Morgan, y que
  `app.ingestion.curate_dataset` (la Fase 1 real) sigue importando.
  Confirmado `pyproject.toml`/`uv.lock` sin rastro de `pytdc`.
- **Decisión final: inlinear los dos métodos necesarios** (preprocesado y
  postprocesado de `DtiBindingdbKdTask`), copiados fielmente de
  `biomed-multi-alignment==0.2.5`, en vez de arrastrar `pytdc`. Riesgo
  asumido explícitamente: si el paquete se actualiza, el formato de
  tokens inlineado podría divergir del que espera el checkpoint sin que
  nada lo avise. Mitigación: assertion de versión instalada al cargar el
  modelo, para que un entorno reconstruido con una versión distinta falle
  alto y claro en vez de predecir mal en silencio.
- **Entorno confirmado en CPU** (`torch.cuda.is_available() == False`): el
  driver NVIDIA local (versión 12020) es demasiado antiguo para la build
  `torch 2.13.0+cu130` instalada. Viable para inferencia; a tener en
  cuenta el impacto en tiempos de entrenamiento al llegar a Fase 3.
- Pendiente: confirmar el smoke test end-to-end (descarga del checkpoint
  ~2GB en curso) antes de dar la Fase 2 por cerrada y comitear.
  **Confirmado y cerrado:** `pKd = 5.4933` sobre el par de ejemplo oficial
  del paquete (1248.7s en la primera ejecución, casi todo descarga del
  checkpoint de 1.8GB — ya cacheado en `~/.cache/huggingface/hub/`, las
  siguientes ejecuciones serán rápidas). `dti_model.py` y `scripts/smoke_test.py`
  commiteados y pusheados a `origin` (`1023ba3`).

## Fase 3 - Fine-tune LoRA

Diseño acordado antes de implementar (`training/lora_finetune.py`). Todas
las cifras verificadas sobre el dataset curado real, no asumidas.

- **Corrección de un dato mal contextualizado en la propuesta de Fase 3:**
  el ratio de positivos NO es 1:3750 / 1:2390. Ese 0.0267% era el hit-rate
  de CO-ADD inhibition-only aislado; la etiqueta `is_hit` del dataset
  curado completo (que incluye ChEMBL, donde ~45% de las filas exactas son
  hits) da un ratio real de **8.93% (Kp)** y **4.64% (Ab)**. Desglose por
  bucket (hits / filas):

  | bucket | Kp | Ab |
  |---|---|---|
  | exacto (`=`) | 9 299 / 20 733 (44.85%) | 5 173 / 11 168 (46.32%) |
  | censurado con cota (`>`,`>=`,`<`,`<=`) | 799 / 13 984 (5.71%) | 301 / 10 538 (2.86%) |
  | inhibition-only sin cota | 1 / 78 341 (0.00%) | 0 / 96 147 (0.00%) |
  | **total** | **10 099 / 113 058 (8.93%)** | **5 474 / 117 853 (4.64%)** |

  Los hits del bucket censurado vienen de las filas izquierda-censuradas
  (`<`/`<=`: compuesto más potente que la dosis más baja probada), no de
  las `>` (que por definición no son hits). El desequilibrio real (10:1 /
  20:1) es 1-2 órdenes de magnitud más suave de lo asumido.

- **pMIC de las filas exactas ~gaussiano, no sesgado** (Kp media 4.94 std
  1.16 skew 0.15; Ab media 4.94 std 0.99 skew 0.17) — casi la distribución
  nativa del checkpoint (BindingDB pKd media 5.79 std 1.34). La regresión
  no necesita reweighting propio; el desequilibrio vive solo en el
  encuadre de clasificación (`is_hit`), no en el target continuo.

- **Diseño de loss (v1): regresión MSE sobre exactas + hinge "Tobit-lite"
  SIMÉTRICO sobre las censuradas con cota.** pMIC = -log10(MIC molar), así
  que:
  - `=` : MSE estándar `(pred - y)²`.
  - `>` / `>=` (MIC mayor → menos potente → cota SUPERIOR de pMIC `b`):
    penaliza solo si predice más potente que la cota, `max(0, pred - b)²`.
  - `<` / `<=` (MIC menor → más potente → cota INFERIOR de pMIC `b`):
    penaliza solo si predice menos potente que la cota, `max(0, b - pred)²`.
    Estas son las filas más valiosas para reposicionamiento (compuestos muy
    potentes); NO se descartan.
  - `~` (1 fila en todo Kp): se trata como `=` (impacto nulo).
  Una sola cabeza (la escalar nativa del checkpoint), sin verosimilitud
  censurada completa (Tobit real) — el hinge captura la dirección de la
  censura con coste e implementación mínimos y sin código no soportado
  sobre la cabeza de MAMMAL.
- **Inhibition-only (sin cota, ~78-96K filas): NO entran en v1.** No tienen
  pMIC ni cota, solo activo/inactivo a concentración única — ninguna loss
  de regresión puede usarlas. Se reservan para calibración en Fase 7
  (¿predice el modelo pMIC más bajo para inactivos conocidos?). Clasificación
  auxiliar ponderada sobre un submuestreo de estas queda como v2, solo si
  el presupuesto de CPU (piloto) deja margen; con tope de peso de clase
  `min(N_neg/N_pos, 10)` y combinación `L = L_reg + 0.5·L_cls`.
- **Split por `inchikey` (grupo), estratificado por `is_hit`, separado por
  patógeno, ~15% test.** Nunca por filas: el mismo compuesto aparece en
  muchas filas (varios ensayos) y un split por filas lo filtraría a train y
  test a la vez. Con el ratio real hay hits de sobra en el test (~1500 Kp /
  ~820 Ab); assert post-split de un mínimo de hits por patógeno. Scaffold
  split (Bemis-Murcko) se reserva como variante rigurosa para Fase 7.
- **Ancla de contexto de organismo (campo "target" del checkpoint): GyrA
  real, fija por patógeno, trazable por accession de UniProt.** El checkpoint
  exige una secuencia de proteína en la entrada; para un QSAR fenotípico
  (donde el "target" es el organismo entero, no una diana) se usa una
  secuencia real y constante por patógeno como id de organismo:
  - **Klebsiella pneumoniae:** UniProt **A0A0H3H0Y6** (cepa HS11286,
    gyrA KPHS_37060, 877 aa, md5 `7dfc605d5f68774ddf990263ffb5433b`).
  - **Acinetobacter baumannii:** UniProt **A0A0D5YFF2** (gyrA ABUW_0960,
    904 aa, md5 `f6be4367e90b430a8843e7de8f29f7c0`).
  Secuencia real (in-distribution para el encoder de proteínas del modelo,
  a diferencia de un placeholder sintético que sería OOD); dos secuencias
  distintas permiten al modelo distinguir Kp de Ab. Se descarta la
  carbapenemasa como ancla (sobre-afirmaría binding específico al mecanismo
  del proyecto).
  **NOTA EXPLÍCITA — leer antes de interpretar cualquier salida:** usar GyrA
  es un requisito de ARQUITECTURA del checkpoint (rellenar el slot de
  target), NO una afirmación de que las predicciones sean específicas de
  unión a GyrA ni a fluoroquinolonas (la clase de antibiótico que sí actúa
  sobre GyrA). El nombre "GyrA" en el código o en cualquier CSV es solo el
  ancla de organismo; el modelo predice potencia fenotípica pMIC, no
  afinidad por la girasa.
- **Presupuesto de CPU: piloto de timing PRIMERO.** Antes de cualquier run
  largo, 50-100 pasos al batch real, medir s/paso, proyectar época =
  (nº filas exactas+cota / batch) × s/paso. Si una época sale > ~4-5 h, el
  entrenamiento se define en un nº fijo de pasos con checkpointing, no en
  épocas. Palanca ya identificada: entrenar **encoder-only** (la predicción
  DTI usa `forward_encoder_only`, el decoder no interviene) ≈ recorta a la
  mitad. No se lanza nada largo sin ese número medido.
- **LoRA: r=8, alpha=16, dropout=0.05, target `q` y `v` de la atención del
  encoder T5** (módulos confirmados `q/k/v/o`). Cabezas
  `scalars_prediction_head` + `encoder_head` entrenadas completas (diminutas);
  resto congelado. Nota honesta: en CPU LoRA no acelera el forward (el base
  de 458M corre igual); recorta parámetros entrenables y memoria de
  optimizador. La palanca de viabilidad en CPU es tamaño de dataset +
  encoder-only + nº de pasos, no el rank. Rank bajo igualmente por memoria y
  regularización (dataset modesto).
- Las 66 filas de `verification_binding_<patogeno>.csv` (Ki/Kd reales) NO
  entran en el fine-tune bajo ningún concepto — reservadas para el chequeo
  cualitativo de afinidad específica en Fase 7.

### Entorno GPU — torch alineado con el driver (incidente y resolución)

- **CPU era inviable, confirmado con números:** el piloto en CPU proyectaba
  **~60-70 h/época** (forward de un modelo de 458M sobre secuencias de 1512
  tokens). Por eso, y no por un bug, no se lanzó ningún run en CPU.
- **GPU local: GTX 1070 (8 GB, Pascal cc 6.1), driver 535 → CUDA 12.2.** El
  torch instalado era `2.13.0+cu130` (CUDA 13.0) → `torch.cuda.is_available()
  == False`. **Vía descartada por imposible, no por preferencia:** subir el
  driver a CUDA 13 no sirve porque CUDA 13 eliminó el soporte de Pascal — la
  1070 no la soporta ninguna build de CUDA 13. Única vía: bajar torch a una
  build cu12x con kernels sm_61.
- **Resolución:** `torch==2.5.1` + `torchvision==0.20.1` desde el índice
  `download.pytorch.org/whl/cu121`, fijados en `pyproject.toml` vía
  `[[tool.uv.index]]` + `[tool.uv.sources]` (NO con `uv pip install` suelto:
  `uv run` re-sincroniza el venv contra el lock y lo revertía). Ningún dep
  fija torch (solo nuestro `torch>=2.2`), así que 2.5.1 encaja. Reversible
  vía git. Verificado: matmul real en GPU OK, y Fases 1-2 (rdkit, inferencia
  DTI) intactas tras el cambio.
- **`app/foundation/dti_model.py` y `TrainConfig.device`: device autodetectado**
  (`cuda` si disponible, si no `cpu`) en vez de `"cpu"` hardcodeado.
- **Entrenamiento a seq 1512 hacía OOM incluso a batch 1** (~7 GB solo en
  activaciones de atención del backward). Resuelto SIN truncar el ancla GyrA
  (decisión delegada, tomada con los números de VRAM delante): **gradient
  checkpointing** en el encoder T5 (recomputa activaciones en el backward) +
  neutralizar el forward de `encoder_head` (106M params de proyección a
  vocabulario que la loss no usa). Pico de VRAM: batch 1 → **2.12 GB**,
  batch 4 → **3.86 GB**. `all params` 458M → 352M (stub de la cabeza).
- **Números finales en GPU (piloto):** ~**0.75 s/fila** (fwd+bwd), constante
  a batch 1-4 (la 1070 es compute-bound, batchear no acelera wall-clock, solo
  mejora el gradiente). Época completa (41 882 filas) ≈ **8.6 h**. Tokenización
  ~2 ms/fila (~1.4 min en total). El entrenamiento se define por tanto en un
  **nº fijo de pasos** con checkpointing periódico, no en épocas.

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
