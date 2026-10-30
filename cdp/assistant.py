"""'Ask your data': plain-English questions -> SQL -> table/chart -> short summary.

Safety model
  * Each tenant gets its own in-memory DuckDB holding ONLY that tenant's gold tables
    (tenant_id column removed). The model cannot reach another tenant's rows because
    they are not in the database it queries.
  * External file/network access is disabled on that database after loading.
  * Only a single read-only SELECT / WITH statement is executed, with a row limit.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import anthropic
import duckdb
import pandas as pd

from cdp.config import AS_OF_DATE, WAREHOUSE_PATH, brand

MODEL = "claude-opus-5-5"
MAX_ROWS = 500

TABLE_DOCS = {
    "customer_360": (
        "One row per resolved customer (the Customer 360 profile).",
        {
            "customer_id": "Unified customer id after identity resolution",
            "full_name": "Best-known name", "email": "Primary email", "phone": "10-digit mobile",
            "city": "City", "region": "Sales region from CRM (West/South/North/East)",
            "lead_source": "How the customer was acquired (CRM)", "accepts_marketing": "Opted in to marketing",
            "sources": "Source systems the person appears in (comma separated)",
            "first_order_date": "First completed order date", "last_order_date": "Most recent completed order date",
            "orders": "Number of completed orders", "refunded_orders": "Number of refunded orders",
            "lifetime_revenue": "Net revenue from completed orders (INR)", "avg_order_value": "Average order value (INR)",
            "avg_days_between_orders": "Average gap between orders in days",
            "preferred_channel": "Web, App or Store", "favourite_category": "Category with the most spend",
            "support_tickets": "Support tickets raised", "open_tickets": "Tickets not yet solved",
            "avg_csat": "Average support satisfaction 1-5", "web_sessions": "Identified web/app sessions",
            "web_sessions_recent": "Sessions in the last 90 days", "abandoned_carts": "Sessions with cart but no purchase",
            "recency_days": "Days since last completed order",
            "r_score": "Recency score 1-5 (5 = most recent)", "f_score": "Frequency score 1-5", "m_score": "Monetary score 1-5",
            "rfm_segment": "Champion, Loyal, Potential Loyalist, New Customer, Promising, Needs Attention, At Risk, "
                           "Cannot Lose Them, Hibernating, Lost, Prospect",
            "tier": "Platinum, Gold, Silver, Bronze, Prospect (by lifetime revenue)",
            "lifecycle_stage": "Prospect, New, Active, Lapsing, Churned",
            "churn_risk": "High, Medium, Low (NULL for prospects)",
            "annualized_value": "Lifetime revenue per year of tenure (INR)",
        },
    ),
    "segment_summary": ("Customers, revenue and averages per rfm_segment x tier.", {}),
    "revenue_monthly": (
        "Monthly orders/customers/net_revenue by channel, customer_type (New/Returning/Unidentified), rfm_segment, tier. "
        "Sum across rows; month is the first day of the month.", {}),
    "product_performance": (
        "One row per product (sku): units_sold, orders, buyers, net_revenue, gross_margin, margin_pct, refund_rate, "
        "support_tickets, avg_ticket_csat, product_views, add_to_carts.", {}),
    "cohort_retention": (
        "Acquisition cohort (month of first order) x months_since_first: cohort_size, active_customers, retention_rate (0-1).",
        {}),
    "funnel_monthly": (
        "Web/app funnel per month and device: sessions, product_views, add_to_carts, checkouts, purchases.", {}),
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "One DuckDB SELECT (or WITH ... SELECT) statement."},
        "explanation": {"type": "string", "description": "One or two plain-English sentences on how the answer is computed."},
        "title": {"type": "string", "description": "Short title for the result."},
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bar", "line", "pie", "scatter", "table", "metric"]},
                "x": {"type": "string", "description": "Column for the x-axis / labels, or empty."},
                "y": {"type": "string", "description": "Numeric column for the y-axis / values, or empty."},
                "color": {"type": "string", "description": "Optional column to colour/group by, or empty."},
            },
            "required": ["type", "x", "y", "color"],
            "additionalProperties": False,
        },
    },
    "required": ["sql", "explanation", "title", "chart"],
    "additionalProperties": False,
}

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|install|load|pragma|set|call|"
    r"read_csv|read_json|read_parquet|read_text|glob)\b", re.I)


@dataclass
class Answer:
    question: str
    sql: str = ""
    explanation: str = ""
    title: str = ""
    chart: dict = field(default_factory=dict)
    data: pd.DataFrame | None = None
    summary: str = ""
    error: str = ""


def tenant_database(tenant_id: str) -> duckdb.DuckDBPyConnection:
    """In-memory DB containing only this tenant's gold tables."""
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{WAREHOUSE_PATH.as_posix()}' AS wh (READ_ONLY)")
    for table in TABLE_DOCS:
        con.execute(f"CREATE TABLE {table} AS SELECT * EXCLUDE (tenant_id) FROM wh.gold.{table} WHERE tenant_id = ?",
                    [tenant_id])
    con.execute("DETACH wh")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def schema_prompt(con: duckdb.DuckDBPyConnection) -> str:
    parts = []
    for table, (doc, col_docs) in TABLE_DOCS.items():
        cols = con.execute(f"DESCRIBE {table}").fetchall()
        lines = [f"  - {name} {dtype}" + (f": {col_docs[name]}" if name in col_docs else "") for name, dtype, *_ in cols]
        parts.append(f"TABLE {table} -- {doc}\n" + "\n".join(lines))
    cats = con.execute("SELECT string_agg(DISTINCT category, ', ') FROM product_performance").fetchall()[0][0]
    cities = con.execute("SELECT string_agg(DISTINCT city, ', ') FROM customer_360").fetchall()[0][0]
    parts.append(f"Known product categories: {cats}\nKnown cities: {cities}")
    return "\n\n".join(parts)


def validate_sql(sql: str) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    # Check keywords on the query with quoted text and comments removed, so a value such as
    # 'Set up help' is not mistaken for a SET statement.
    code = re.sub(r"'(?:[^']|'')*'", "''", cleaned)
    code = re.sub(r"--[^\n]*|/\*.*?\*/", " ", code, flags=re.S)
    if ";" in code:
        raise ValueError("Only a single statement is allowed.")
    if not re.match(r"^\s*(with|select)\b", code, re.I):
        raise ValueError("Only SELECT queries are allowed.")
    if _FORBIDDEN.search(code):
        raise ValueError("The query uses a statement or function that is not allowed.")
    return cleaned


def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    cleaned = validate_sql(sql)
    return con.cursor().execute(f"SELECT * FROM ({cleaned}) LIMIT {MAX_ROWS}").df()


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def provider() -> str | None:
    """Which LLM answers questions: CDP_AI_PROVIDER if set, else Claude, else Gemini, by available keys."""
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                      or os.environ.get("ANTHROPIC_PROFILE"))
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    wanted = os.environ.get("CDP_AI_PROVIDER", "").lower()
    if wanted == "gemini" and has_gemini:
        return "gemini"
    if wanted == "claude" and has_claude:
        return "claude"
    return "claude" if has_claude else ("gemini" if has_gemini else None)


def provider_label() -> str:
    return {"claude": f"Claude ({MODEL})", "gemini": f"Gemini ({GEMINI_MODEL})"}.get(provider(), "none")


def has_credentials() -> bool:
    return provider() is not None


def _system_prompt(tenant_name: str, schema: str) -> str:
    return (
        f"You are the data analyst assistant inside {brand()['product_name']}, a customer data platform, for the business "
        f"'{tenant_name}'. Business users ask questions in plain English; you answer by writing one DuckDB SQL "
        f"query over the tables below. Today's date for all analysis is {AS_OF_DATE.isoformat()} (use this, not "
        f"current_date). Money is in INR.\n\n"
        "Rules:\n"
        "- Use only these tables and columns. There is no tenant_id column; the data is already scoped to this business.\n"
        "- Write a single SELECT or WITH query. Round money to 0 decimals and rates to 1 decimal percent when shown.\n"
        "- Return at most ~50 rows for lists unless the user asks for more; order results meaningfully.\n"
        "- Use readable column aliases (snake_case), because they become chart axes.\n"
        "- Pick the chart that fits: line for trends over time, bar for comparisons, pie only for shares of a "
        "whole with few slices, metric for a single number, table for lists of customers or products.\n"
        "- If the question cannot be answered from these tables, write a query that returns the closest useful "
        "data and say so in the explanation.\n\n"
        f"Schema:\n{schema}"
    )


def _claude(system: str, messages: list[dict], max_tokens: int, effort: str, schema: dict | None) -> str:
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        response = anthropic.Anthropic().beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            cache_control={"type": "ephemeral"},
        )
    except (anthropic.APIConnectionError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise RuntimeError(f"Could not reach Claude: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer this question.")
    return "".join(b.text for b in response.content if b.type == "text")


def _gemini(system: str, messages: list[dict], max_tokens: int, effort: str, schema: dict | None) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                     for m in messages],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if schema:
        body["generationConfig"].update({"responseMimeType": "application/json", "responseJsonSchema": schema})
    request = urllib.request.Request(
        GEMINI_URL.format(model=GEMINI_MODEL), data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Gemini API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Gemini: {exc.reason}") from exc
    candidates = data.get("candidates") or []
    if not candidates or not candidates[0].get("content"):
        reason = (candidates[0].get("finishReason") if candidates else None) or data.get("promptFeedback")
        raise RuntimeError(f"Gemini returned no answer ({reason}).")
    return "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []))


def _complete(system: str, messages: list[dict], max_tokens: int, effort: str, schema: dict | None = None) -> str:
    backend = _gemini if provider() == "gemini" else _claude
    return backend(system, messages, max_tokens, effort, schema)


def ask(con: duckdb.DuckDBPyConnection, tenant_name: str, schema: str, question: str,
        history: list[dict] | None = None, summarize: bool = True) -> Answer:
    """Generate SQL for `question`, run it (one self-correction retry on error) and summarize."""
    answer = Answer(question=question)
    system = _system_prompt(tenant_name, schema)
    messages = list(history or []) + [{"role": "user", "content": question}]

    for attempt in range(2):
        try:
            raw = _complete(system, messages, max_tokens=4000, effort="medium", schema=ANSWER_SCHEMA)
            plan = json.loads(raw)
        except (RuntimeError, json.JSONDecodeError) as exc:
            answer.error = str(exc)
            return answer

        answer.sql, answer.explanation = plan["sql"], plan["explanation"]
        answer.title, answer.chart = plan["title"], plan["chart"]
        try:
            answer.data = run_sql(con, answer.sql)
            answer.error = ""
            break
        except Exception as exc:  # duckdb errors or validation errors -> let the model fix it once
            answer.error = f"Query failed: {exc}"
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That query failed with this error:\n{exc}\nPlease return a corrected query."},
            ]

    if answer.data is not None and summarize and len(answer.data):
        try:
            preview = answer.data.head(30).to_csv(index=False)
            answer.summary = _complete(
                "You summarize query results for business users in 2-4 short sentences. Mention the key "
                "numbers and one practical takeaway. No preamble, no markdown headings.",
                [{"role": "user", "content": f"Question: {question}\n\nResult ({len(answer.data)} rows, "
                                             f"first 30 shown):\n{preview}"}],
                max_tokens=1500, effort="low",
            ).strip()
        except RuntimeError:
            answer.summary = ""
    return answer


# Pre-written examples so the page still demonstrates the flow without API credentials.
EXAMPLES = {
    "Who are my top 10 customers by lifetime revenue?": (
        "SELECT full_name, city, tier, rfm_segment, orders, round(lifetime_revenue) AS lifetime_revenue "
        "FROM customer_360 ORDER BY lifetime_revenue DESC LIMIT 10",
        {"type": "bar", "x": "full_name", "y": "lifetime_revenue", "color": "tier"}),
    "How many customers are in each RFM segment and how much revenue do they bring?": (
        "SELECT rfm_segment, sum(customers) AS customers, round(sum(revenue)) AS revenue "
        "FROM segment_summary GROUP BY 1 ORDER BY revenue DESC",
        {"type": "bar", "x": "rfm_segment", "y": "revenue", "color": ""}),
    "Show monthly revenue for the last 12 months by channel": (
        "SELECT month, channel, round(sum(net_revenue)) AS net_revenue FROM revenue_monthly "
        f"WHERE month > DATE '{AS_OF_DATE.isoformat()}' - INTERVAL 12 MONTH GROUP BY 1, 2 ORDER BY 1",
        {"type": "line", "x": "month", "y": "net_revenue", "color": "channel"}),
    "Which Gold or Platinum customers are at high churn risk?": (
        "SELECT full_name, email, city, tier, recency_days, orders, round(lifetime_revenue) AS lifetime_revenue "
        "FROM customer_360 WHERE tier IN ('Gold', 'Platinum') AND churn_risk = 'High' ORDER BY lifetime_revenue DESC",
        {"type": "table", "x": "", "y": "", "color": ""}),
    "Which products have the highest refund rate?": (
        "SELECT product_name, category, orders, round(refund_rate * 100, 1) AS refund_rate_pct, support_tickets "
        "FROM product_performance WHERE orders >= 20 ORDER BY refund_rate DESC LIMIT 10",
        {"type": "bar", "x": "product_name", "y": "refund_rate_pct", "color": "category"}),
    "Revenue by city": (
        "SELECT city, count(*) AS customers, round(sum(lifetime_revenue)) AS revenue FROM customer_360 "
        "WHERE orders > 0 GROUP BY 1 ORDER BY revenue DESC",
        {"type": "pie", "x": "city", "y": "revenue", "color": ""}),
}


def example_answer(con: duckdb.DuckDBPyConnection, question: str) -> Answer:
    sql, chart = EXAMPLES[question]
    answer = Answer(question=question, sql=sql, chart=chart, title=question,
                    explanation="Pre-written example query (offline mode).")
    answer.data = run_sql(con, sql)
    return answer
