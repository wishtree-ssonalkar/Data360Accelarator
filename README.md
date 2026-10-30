# WishUnify – Customer Data Platform accelerator

*by Wishtree Technologies*

WishUnify connects a client's customer data from all its systems, works out which records belong to
the same person, and builds **one Customer 360 profile per customer**. On top of that it adds RFM
segmentation, tiers and churn risk, ready-made analytics, and an AI assistant that answers plain-English
questions with SQL.

It's built as a reusable accelerator: **a new client is one config file, and a new kind of source system
is one mapping file.** The engine, models, app and AI are the same for every client.

> The product name lives in `config/platform.json` → `branding`. Change it there; the code package (`cdp`)
> is deliberately brand-neutral.

## Architecture

```
 Client systems        1 CONNECT           2 MAP (config)          3 RESOLVE + 360         4/5 USE
 Shopify, POS,   ──►  file / database /  ──► source_types/*.json ──► identity graph,     ──► dashboards,
 CRM, helpdesk,       REST connectors        → standard model         golden record,         audiences,
 web events, DBs      (bronze, raw)          (silver.std_*)           RFM, churn (gold)      AI questions
                                    6 MULTI-TENANT: every table carries tenant_id; rules per client
```

| Layer | What it does | Where |
|---|---|---|
| **Connect** | Files (local, folders, S3, Azure Blob/ADLS, GCS, HTTPS; CSV/JSON/Parquet/Excel), databases (SQL Server, Oracle and any ODBC database; PostgreSQL; MySQL; SQLite), REST APIs. Full or incremental loads. | `cdp/connectors.py` |
| **Map** | Each source type maps raw columns to the standard model with SQL expressions; the SQL is generated. | `config/source_types/`, `cdp/mapping.py` |
| **Standard model** | customers, orders, order_lines, products, events, tickets | `cdp/model.py` |
| **Identity resolution** | Links records sharing a source customer id, email or phone into one `customer_id` | `cdp/identity.py` |
| **Customer 360** | Golden record (by source priority), RFM, segments, tiers, lifecycle, churn risk | `sql/silver/`, `sql/gold/` |
| **Analytics + AI** | Overview, Customer 360, segments/audiences, retention, funnel, products, Ask your data | `app/`, `cdp/assistant.py` |

### Production behaviour

- **Config validation** before every run, listing every problem at once (`python -m cdp validate`).
- **Failure isolation:** a broken source is logged and skipped, its last good data stays in use, and the run ends as `partial`.
- **Empty-extract protection:** a full load that returns 0 rows doesn't wipe existing data.
- **Incremental loads** with a stored watermark per source, plus de-duplication by key.
- **Zero-downtime refresh:** each run builds a copy and swaps it in when done. The app stays online, and a failed run never touches live data.
- **Run history** (`meta.runs`, `meta.source_runs`), **data-quality checks** (`meta.dq_results`), **JSON logs** (`data/logs/pipeline.log`).
- **Secrets only in environment variables**: config files reference them by name.
- **AI safety:** the assistant gets only the selected client's Gold tables, read-only, with file/network access off, and may run a single `SELECT`.

## Run locally

```bash
python -m venv --system-site-packages .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m cdp sample-data
.venv\Scripts\python -m cdp run
.venv\Scripts\streamlit run app\streamlit_app.py
```

CLI: `validate`, `run [--tenant ID] [--full-refresh]`, `schedule --every-minutes N`, `status`, `sample-data`.
Exit code is 0 on success, 2 on partial (some sources failed) and 1 on failure, for use by schedulers and alerts.

Tests: `pytest -q`. They cover file, database (incremental) and REST sources, a broken source, identity, the golden record, config validation and AI SQL safety.

## Deploy (Docker)

```bash
cp .env.example .env                       # add AI key + source credentials
docker compose build
docker compose run --rm cli sample-data     # demo data only
docker compose run --rm pipeline-once       # first load
docker compose up -d app scheduler          # app on :8501, refresh every CDP_REFRESH_MINUTES
```

CI (`.github/workflows/ci.yml`) validates config, runs the tests, runs the sample pipeline and builds the image.

## Onboarding a client

Done from the app's **Admin** screens: Clients, Sources & connections (with *Test connection* and file upload),
Mappings (column picker with live preview), Credentials (encrypted) and *Run now*. No server access needed.
See **[docs/ONBOARDING.md](docs/ONBOARDING.md)**.

Admin security: `CDP_ADMIN_PASSWORD` protects the admin screens (locked in production until set), and
`CDP_SECRET_KEY` encrypts stored credentials (`python -m cdp secret-key`). Config edits are validated, versioned
in `config/.history/` and audit-logged.

## AI assistant

Set `ANTHROPIC_API_KEY` (Claude) or `GEMINI_API_KEY` (Gemini; `GEMINI_MODEL` to change model). If both are set,
Claude is used unless `CDP_AI_PROVIDER=gemini`. Without a key, the example questions still work.

## Not yet included

- **Login / SSO and per-user permissions.** Put the app behind your SSO proxy (e.g. Entra ID App Proxy, oauth2-proxy) until built-in roles are added.
- **Fuzzy identity matching** (names, addresses). Matching today is exact id/email/phone.
- **Big-data scale-out.** DuckDB handles tens of millions of rows on one server. For larger clients, port the SQL models to Databricks/Snowflake; they're plain SQL.
- **Activation:** pushing audiences to ad and email platforms. CSV export only today.

## Layout

```
config/platform.json        branding, paths, as-of date, default business rules
config/tenants/*.json       one file per client: sources, connectors, rule overrides
config/source_types/*.json  one file per kind of system: how to map it to the standard model
cdp/                        connectors, mapping engine, identity, pipeline, CLI, AI assistant
sql/silver, sql/gold        models over the standard tables
app/                        Streamlit app
tests/                      pytest suite
docs/ONBOARDING.md          how to add a client or a source
```
