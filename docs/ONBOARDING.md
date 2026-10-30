# Onboarding a client

You need to change code **only** if a client needs a new entity or KPI. Everything below is configuration.

## The easy way: the Admin screens

Everything here can be done in the app, with no server or file access:

1. **Admin > Clients**: create the client and set its business rules.
2. **Admin > Credentials & history**: store database connection strings, passwords and API tokens
   (encrypted; values are never shown again).
3. **Admin > Mappings**: only for a system you haven't connected before. Load a sample (upload a file or read
   a configured source), pick the matching source column for each standard column (common names are
   suggested automatically), check the live result, save.
4. **Admin > Sources & connections**: add each source (upload files, a path or cloud URL, a database, or
   an API), click **Test connection** to see raw rows *and* the mapped result, then save.
5. **Data pipeline > Run now**: load the data. Each source's status and errors show on the same page.

Every save is validated against the whole configuration first. The previous version is kept in
`config/.history/`, and who changed what is shown under **Credentials & history > Change history**.

**Security for deployments:** set `CDP_ADMIN_PASSWORD` (admin sign-in) and `CDP_SECRET_KEY` (credential
encryption; generate it with `python -m cdp secret-key` and keep a safe copy). With `CDP_ENV=production`, the
default in the Docker image, the admin screens stay locked until a password is set.

The rest of this guide describes the same settings as files, for automation or version control.

## 1. Create the client file

`config/tenants/<client_id>.json` (lowercase id, letters/digits/underscores):

```json
{
  "tenant_id": "acme",
  "name": "Acme Retail",
  "industry": "Fashion retail",
  "currency": "INR",
  "phone_digits": 10,
  "business_rules": { "active_window_days": 120, "tier_thresholds": { "Platinum": 100000 } },
  "sources": [ ]
}
```

`business_rules` overrides the defaults in `config/platform.json` (active window, churn thresholds, tier
thresholds, recent-activity window). Leave it `{}` to use the defaults.

## 2. Add each source

Each entry in `sources` has a unique `name`, a `type` (a file in `config/source_types/`), a `connector`,
and an optional `priority` (customer sources only: lower wins when sources disagree on a name, email etc.).

### Files

```json
{ "name": "shopify_customers", "type": "shopify_customers", "priority": 1,
  "connector": { "kind": "file", "path": "shopify/customers_*.csv" } }
```

- Relative paths are under `data/sources/<client_id>/`; globs read whole folders.
- Cloud: `"path": "s3://bucket/crm/*.parquet"` (AWS credential chain), `"az://container/path/*.csv"` or
  `abfss://...` (`AZURE_STORAGE_CONNECTION_STRING`, or the Azure credential chain with `AZURE_STORAGE_ACCOUNT`),
  `gs://...` (`GCS_HMAC_KEY_ID` / `GCS_HMAC_SECRET`).
- Override the format with `"format": "parquet" | "csv" | "jsonl" | "json" | "excel"`; for Excel, also `"read": {"sheet": "Sheet1"}`.

### Databases

```json
{ "name": "erp_orders", "type": "acme_erp_orders",
  "connector": { "kind": "database", "engine": "sqlserver", "connection_env": "ACME_ERP",
                 "query": "SELECT * FROM sales.orders WHERE company = 'IN'",
                 "incremental": { "column": "modified_at" } } }
```

- `engine`: `sqlserver`, `oracle`, `odbc` (any ODBC driver; the env var holds the ODBC connection string),
  `postgres`, `mysql`, `sqlite`.
- `table` or `query`. Put the connection string in the environment variable named by `connection_env`
  (in `.env` for Docker). Never put it in the JSON.
- `incremental.column`: only rows with a larger value than last time are fetched and appended.
  `python -m cdp run --full-refresh` reloads everything.

### REST APIs

```json
{ "name": "helpdesk", "type": "acme_helpdesk_tickets",
  "connector": { "kind": "rest", "url": "https://api.helpdesk.com/v2/tickets",
                 "headers": { "Authorization": "Bearer ${ACME_HELPDESK_TOKEN}" },
                 "records_path": "data.tickets",
                 "pagination": { "type": "page", "param": "page", "size_param": "per_page", "size": 100 },
                 "incremental": { "param": "updated_since" } } }
```

Pagination can also be `{"type": "next_url", "path": "links.next"}`. `${VAR}` is read from the environment.

**Large SaaS systems (Salesforce, HubSpot, Dynamics, Google Ads, …):** land them with an ingestion tool
(Airbyte, Fivetran, dlt or Azure Data Factory) into files or a database, then use the file or database
connector. That's faster and more robust than writing a custom API client for each.

## 3. If the system is new, add a source type

`config/source_types/<type>.json` says how raw columns become standard columns. Values are DuckDB SQL
expressions over the raw columns, which arrive as text:

```json
{
  "description": "Acme ERP order header table",
  "system": "Acme ERP",
  "outputs": [{
    "entity": "orders",
    "customer_namespace": "acme_erp",
    "columns": {
      "order_id": "OrderNo",
      "customer_source_key": "CustNo",
      "order_ts": "strptime(OrderDate, '%d-%m-%Y %H:%M')",
      "channel": "CASE SalesChannel WHEN 'ECOM' THEN 'Web' ELSE 'Store' END",
      "status": "CASE WHEN Cancelled = '1' THEN 'cancelled' WHEN Returned = '1' THEN 'refunded' ELSE 'completed' END",
      "order_value": "CAST(NetAmount AS DECIMAL(14,2))"
    }
  }]
}
```

- **Entities and their columns** are listed in `cdp/model.py`; required ones must be mapped.
- One source can feed several entities (see `pos_sales_lines.json`: one file → orders + order lines,
  using `group_by`).
- **Linking to customers:** a customer source sets `"namespace"` (e.g. `acme_erp`); rows in other sources
  that hold that system's customer id map it to `customer_source_key` with the same `customer_namespace`.
  Rows with only an email or phone are linked automatically.
- `status` must end up as `completed`, `refunded` or `cancelled`. The data-quality checks flag anything else.

## 4. Validate, load, check

```bash
python -m cdp validate              # every config problem at once
python -m cdp run --tenant acme     # load this client (models rebuild for all)
python -m cdp status                # per-source rows / errors
```

In the app, open **Data pipeline** for that client: the Sources tab shows each source's last load and
any error, Identity resolution shows how many records merged, and Data quality shows the checks.
