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
