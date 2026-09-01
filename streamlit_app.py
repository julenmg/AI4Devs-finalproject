"""Fase 8 - Demo de EskapeGuard.

    uv run streamlit run streamlit_app.py

ENCUADRE: esto no es un producto, es el guion del video de demostracion. La
prioridad es que se pueda grabar sin sorpresas: preguntas precargadas para no
teclear en camara, cargas pesadas cacheadas una sola vez, tipografia legible en
video, y la pestana del caso de estudio funcionando SIN GPU (lee del CSV del
cribado, que va versionado en el repo).

Las tres pestanas siguen la narrativa del proyecto:
  1. CAG    - donde se rompe el contexto fijo.
  2. RAG    - la MISMA pregunta, ahora con evidencia real citada.
  3. Agente - caso de estudio por cubos y consulta libre con traza de tools.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.config import settings

st.set_page_config(page_title="EskapeGuard", page_icon="🧬", layout="wide")

# Tipografia mas grande de lo habitual: el destino es un video comprimido, no
# una pantalla de escritorio.
st.markdown(
    """
    <style>
      html, body, [class*="css"] { font-size: 17px; }
      .stMarkdown p, .stMarkdown li { font-size: 1.05rem; line-height: 1.6; }
      code { font-size: 0.95rem; }
      .stTabs [data-baseweb="tab"] { font-size: 1.1rem; padding: 0.6rem 1.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# La pregunta que se usa en las pestanas 1 y 2. Es la MISMA a proposito: la
# comparacion CAG -> RAG sobre una pregunta identica es el momento clave.
PREGUNTA_CLAVE = (
    "¿Qué evidencia experimental hay sobre el ciprofloxacino frente a "
    "Klebsiella pneumoniae?"
)

PREGUNTAS_RAG = [
    PREGUNTA_CLAVE,
    "¿Hay algún compuesto con afinidad de unión medida contra la carbapenemasa KPC?",
    "¿Cuántos compuestos se cribaron frente a Acinetobacter baumannii y qué "
    "proporción dio señal?",
    "Compara los mecanismos de resistencia a carbapenémicos de los dos patógenos.",
]

PREGUNTAS_AGENTE = [
    "¿Qué compuestos de colección clínica son mejores candidatos de "
    "reposicionamiento frente a Acinetobacter baumannii? Dame los cinco primeros.",
    "¿Qué potencia predice el modelo para el ciprofloxacino frente a Klebsiella "
    "pneumoniae, y qué evidencia experimental real hay sobre ese compuesto?",
    "¿Hay compuestos donde el modelo prediga potencia alta pero el experimento "
    "diga lo contrario? Explícame qué significa eso.",
    "¿Puedo tratar a un paciente con neumonía por Acinetobacter baumannii con el "
    "mejor candidato de tu ranking?",
]

CUBOS = {
    "hipotesis_transferencia": "Hipótesis de transferencia — el candidato genuino",
    "recuperacion": "Recuperación — activos ya confirmados (validan el pipeline)",
    "desacuerdo_modelo_experimento": "Desacuerdo modelo-experimento",
    "concordancia_negativa": "Concordancia negativa",
}


# --------------------------------------------------------------------------- #
# Cargas cacheadas: una sola vez por sesion, no por interaccion.
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def cargar_cribado(pathogen: str) -> pd.DataFrame:
    from app.generation.agentic.tools import load_screen

    return load_screen(pathogen)


@st.cache_resource(show_spinner="Cargando índice RAG…")
def precalentar_rag():
    """Fuerza la carga del modelo de embeddings y del indice en la primera
    llamada, para que las consultas de la demo no paguen ese coste en camara."""
    from app.generation.rag.retrieval import retrieve

    retrieve("calentamiento del indice", k=1)
    return True


def mostrar_verificacion(v: dict, tipo: str) -> None:
    """El resultado del verificador va SIEMPRE a la vista, no escondido: es
    parte de lo que la demo tiene que ensenar."""
    if tipo == "rag":
        ok = v.get("citations_ok", False)
        avisos = v.get("ungrounded_numbers", [])
        etiqueta = (
            f"citas verificadas: {len(v.get('cited_labels', []))} · "
            f"inválidas: {len(v.get('invalid_labels', []))}"
        )
    else:
        ok = v.get("ok", False)
        avisos = v.get("predicciones_alteradas", [])
        etiqueta = f"predicciones citadas: {v.get('predicciones_devueltas', 0)}"

    if ok and not avisos:
        st.success(f"✅ Verificación superada — {etiqueta}")
    elif ok:
        st.warning(f"⚠️ {etiqueta} · cifras a revisar: {avisos}")
    else:
        st.error(f"❌ Verificación fallida — {etiqueta} · {v}")


st.title("EskapeGuard")
st.caption(
    "Priorización de candidatos a reposicionamiento frente a *Klebsiella "
    "pneumoniae* y *Acinetobacter baumannii*. Predice **potencia fenotípica "
    "in vitro**, nunca eficacia clínica."
)

tab_cag, tab_rag, tab_agente = st.tabs(
    ["1 · CAG (dónde se rompe)", "2 · RAG (evidencia citada)", "3 · Agente y caso de estudio"]
)

# --------------------------------------------------------------------------- #
# 1. CAG
# --------------------------------------------------------------------------- #
with tab_cag:
    st.subheader("Contexto fijo, sin recuperación")
    st.markdown(
        "El CAG lleva las fichas de los dos patógenos escritas a mano en el "
        "*system prompt*. No consulta ninguna fuente. Esta pestaña existe para "
        "enseñar **dónde se rompe**: ante una pregunta cuantitativa concreta, "
        "no puede responder — y lo dice en vez de inventarlo."
    )
    pregunta_cag = st.text_area("Pregunta", PREGUNTA_CLAVE, height=80, key="q_cag")
    if st.button("Preguntar al CAG", type="primary", key="btn_cag"):
        from app.generation.cag.static_context import answer_with_static_context

        with st.spinner("Consultando…"):
            st.session_state["resp_cag"] = answer_with_static_context(pregunta_cag)
    if "resp_cag" in st.session_state:
        st.markdown("---")
        st.markdown(st.session_state["resp_cag"])
        st.info(
            "Sin evidencia que citar y sin valores concretos. Esa limitación es "
            "lo que justifica el salto a RAG, no un defecto a corregir aquí."
        )

# --------------------------------------------------------------------------- #
# 2. RAG
# --------------------------------------------------------------------------- #
with tab_rag:
    st.subheader("La misma pregunta, con evidencia real recuperada")
    pregunta_rag = st.selectbox("Pregunta", PREGUNTAS_RAG, index=0, key="q_rag")
    if st.button("Preguntar al RAG", type="primary", key="btn_rag"):
        precalentar_rag()
        from app.generation.rag.retrieval import answer_with_retrieval

        with st.spinner("Recuperando evidencia y respondiendo…"):
            st.session_state["resp_rag"] = answer_with_retrieval(pregunta_rag)

    if "resp_rag" in st.session_state:
        r = st.session_state["resp_rag"]
        st.markdown("---")
        col_resp, col_ev = st.columns([3, 2])
        with col_resp:
            st.markdown(r["answer"])
        with col_ev:
            st.markdown("#### Evidencia recuperada")
            st.caption(
                f"Filtro de patógeno aplicado: **{r['pathogen_filter'] or 'ninguno'}**"
            )
            for ev in r["evidence"]:
                st.markdown(
                    f"**[{ev['label']}]** · `{ev['evidence_class']}` · "
                    f"distancia {ev['distance']}  \n{ev['citation']}"
                )
        st.markdown("---")
        mostrar_verificacion(r["verification"], "rag")
        st.caption(
            "La cita de cada fragmento la construye el código desde la metadata, "
            "nunca el modelo."
        )

# --------------------------------------------------------------------------- #
# 3. Agente y caso de estudio
# --------------------------------------------------------------------------- #
with tab_agente:
    st.subheader("Caso de estudio de reposicionamiento")
    st.markdown(
        "Cribado precomputado sobre los **700 compuestos de colección clínica** "
        "(NIH Clinical Collection) más los activos frente al otro patógeno. Se "
        "clasifican en cubos en vez de en un ranking plano, para no mezclar "
        "*recuperar lo ya conocido* con *proponer lo nuevo*."
    )

    col_p, col_c = st.columns(2)
    pathogen = col_p.selectbox("Patógeno", settings.pathogens, key="pat")
    cubo = col_c.selectbox(
        "Cubo", list(CUBOS), format_func=lambda c: CUBOS[c], key="cubo"
    )

    df = cargar_cribado(pathogen)
    sub = df[df["bucket"] == cubo].nlargest(10, "pred_pmic")

    resumen = df["bucket"].value_counts()
    st.caption(
        " · ".join(f"**{CUBOS.get(k, k).split(' —')[0]}**: {v}" for k, v in resumen.items())
    )

    columnas = {
        "compound_name": "Compuesto",
        "pred_pmic": "pMIC predicho",
        "px_real": "pMIC medido",
        "evidence_level": "Evidencia",
        "seen_in_training": "Visto al entrenar",
        "source_pathogen": "Activo en",
    }
    vista = sub[[c for c in columnas if c in sub.columns]].rename(columns=columnas)
    st.dataframe(vista, use_container_width=True, hide_index=True)
    st.caption(
        "**pMIC predicho** es una predicción del modelo (error típico ~1 unidad). "
        "**pMIC medido** es un dato experimental. No son la misma cosa y el "
        "sistema nunca las reconcilia. *Visto al entrenar* marca los compuestos "
        "para los que acertar no demuestra capacidad predictiva."
    )

    st.markdown("---")
    st.subheader("Pregunta al agente")
    pregunta_ag = st.selectbox("Pregunta", PREGUNTAS_AGENTE, index=0, key="q_ag")
    if st.button("Ejecutar agente", type="primary", key="btn_ag"):
        from app.generation.agentic.agent import run_agent

        with st.spinner("El agente está decidiendo qué herramientas usar…"):
            st.session_state["resp_ag"] = run_agent(pregunta_ag)

    if "resp_ag" in st.session_state:
        r = st.session_state["resp_ag"]
        st.markdown("---")
        st.markdown("#### Herramientas invocadas")
        if not r["tool_calls"]:
            st.write("_Ninguna: el agente respondió sin necesitar herramientas._")
        for i, call in enumerate(r["tool_calls"], start=1):
            st.markdown(f"{i}. `{call['tool']}` — {call['input']}")
        st.markdown("#### Respuesta")
        st.markdown(r["answer"])
        mostrar_verificacion(r["verification"], "agente")
