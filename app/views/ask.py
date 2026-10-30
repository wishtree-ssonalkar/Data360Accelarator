import json

import pandas as pd
import plotly.express as px
import streamlit as st

from common import page_header, style_fig, tenant, tenant_id, warehouse_version
from cdp import assistant

page_header("Ask your data", "Plain-English questions answered with SQL, charts and a summary")


@st.cache_resource(show_spinner="Preparing this business unit's data for the assistant…")
def tenant_db(tid: str, version: float):  # version: rebuilt when a new warehouse is published
    con = assistant.tenant_database(tid)
    return con, assistant.schema_prompt(con)


con, schema = tenant_db(tenant_id(), warehouse_version())
history_key = f"chat_{tenant_id()}"
st.session_state.setdefault(history_key, [])
chat: list[assistant.Answer] = st.session_state[history_key]

online = assistant.has_credentials()
with st.sidebar:
    st.markdown("**Assistant**")
    if online:
        st.success(f"Connected: {assistant.provider_label()}", icon=":material/check_circle:")
    else:
        st.warning("The AI assistant is not set up. An administrator can choose a provider (Claude, Gemini, OpenAI, "
                   "Azure OpenAI or compatible) and add its key under **Admin > AI assistant**. The example "
                   "questions below still work.", icon=":material/key_off:")
    can_summarize = online and assistant.shares_result_rows()
    summarize = st.toggle("Add AI summary to answers", value=can_summarize, disabled=not can_summarize,
                          help=None if can_summarize else "Summaries are off because sending result rows to the AI "
                                                          "provider is disabled in Admin > AI assistant.")
    if st.button("Clear conversation", icon=":material/delete_sweep:", disabled=not chat):
        st.session_state[history_key] = []
        st.rerun()
    st.caption("The assistant only sees this business unit's Gold tables, in a separate read-only copy; it can run "
               "SELECT queries only.")


def render(answer: assistant.Answer) -> None:
    if answer.error and answer.data is None:
        st.error(answer.error)
        if answer.sql:
            st.code(answer.sql, language="sql")
        return
    df: pd.DataFrame = answer.data
    spec = answer.chart or {}
    kind, x, y, color = (spec.get(k) or "" for k in ("type", "x", "y", "color"))
    if answer.title:
        st.markdown(f"**{answer.title}**")
    if answer.summary:
        st.markdown(answer.summary)
    cols = set(df.columns)
    if df.empty:
        st.info("The query returned no rows.")
    elif kind == "metric" and len(df) == 1:
        with st.container(horizontal=True):
            for col in df.columns:
                val = df[col].iat[0]
                st.metric(col.replace("_", " ").capitalize(),
                          f"{val:,.0f}" if isinstance(val, (int, float)) and abs(val) >= 100 else str(val), border=True)
    elif kind in ("bar", "line", "pie", "scatter") and x in cols and y in cols:
        color = color if color in cols else None
        if kind == "bar":
            fig = px.bar(df, x=x, y=y, color=color)
        elif kind == "line":
            fig = px.line(df, x=x, y=y, color=color, markers=True)
        elif kind == "pie":
            fig = px.pie(df, names=x, values=y, hole=0.45)
        else:
            fig = px.scatter(df, x=x, y=y, color=color)
        st.plotly_chart(style_fig(fig, 380), width="stretch", key=f"chart_{id(answer)}")
    if not df.empty and kind not in ("metric",):
        with st.expander(f"Data ({len(df)} rows)", expanded=kind == "table"):
            st.dataframe(df, hide_index=True, width="stretch")
    with st.expander("How this was answered", icon=":material/code:"):
        st.markdown(answer.explanation)
        st.code(answer.sql, language="sql")
        if answer.error:
            st.caption(f"First attempt failed and was corrected: {answer.error}")


def history_messages() -> list[dict]:
    messages = []
    for a in chat[-6:]:
        if a.sql:
            messages.append({"role": "user", "content": a.question})
            messages.append({"role": "assistant", "content": json.dumps(
                {"sql": a.sql, "explanation": a.explanation, "title": a.title, "chart": a.chart})})
    return messages


if not chat:
    st.markdown(f"Ask anything about **{tenant()['name']}** customers, revenue, segments, products, retention or the "
                "funnel. Try one of these:")

picked = st.pills("Example questions", list(assistant.EXAMPLES), label_visibility="collapsed", key=f"pill_{len(chat)}")

for a in chat:
    with st.chat_message("user"):
        st.markdown(a.question)
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        render(a)

question = st.chat_input("e.g. Which cities have the most At Risk customers?", disabled=False) or picked
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        if online:
            with st.spinner("Writing SQL and running it…"):
                answer = assistant.ask(con, tenant()["name"], schema, question, history_messages(), summarize)
        elif question in assistant.EXAMPLES:
            answer = assistant.example_answer(con, question)
        else:
            answer = assistant.Answer(question=question, error="Free-form questions need an AI provider: ask an "
                                                               "administrator to set one up under Admin > AI "
                                                               "assistant. Pick an example question instead.")
        render(answer)
    chat.append(answer)
