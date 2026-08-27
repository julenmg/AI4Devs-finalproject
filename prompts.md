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

---

## 8. Limitaciones y Próximos Pasos

**Prompt 1:**