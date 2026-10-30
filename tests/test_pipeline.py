"""End-to-end: file + database (incremental) + REST sources, one broken source, identity and Customer 360."""
from __future__ import annotations

import sqlite3

import duckdb
import pytest

from conftest import DB_PATH
from cdp.config import WAREHOUSE_PATH
from cdp.pipeline import run


def q(sql: str):
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def first_run():
    return run()


def test_broken_source_does_not_stop_the_run(first_run):
    assert first_run == "partial"
    status = dict(q("SELECT source_name, status FROM meta.source_runs"))
    assert status["legacy_crm"] == "failed"
    assert status["shop_customers"] == status["shop_orders"] == status["helpdesk"] == "success"
    error = q("SELECT error FROM meta.source_runs WHERE source_name = 'legacy_crm'")[0][0]
    assert "File not found" in error


def test_rest_connector_paginates(first_run):
    assert q("SELECT count(*) FROM silver.std_tickets")[0][0] == 3  # 2 pages of 2 + 1


def test_identity_merges_across_sources(first_run):
    # Asha: Shopify customer + DB order + helpdesk ticket (email in different case) -> one customer.
    rows = q("SELECT customer_id, orders, support_tickets, lifetime_revenue FROM gold.customer_360 "
             "WHERE email = 'asha.rao@example.com'")
    assert len(rows) == 1
    _, orders, tickets, revenue = rows[0]
    assert (orders, tickets, float(revenue)) == (1, 1, 5000.0)
    # Ben has two Shopify accounts with different emails; the DB order email matches account S2 only.
    ben = q("SELECT count(DISTINCT customer_id) FROM gold.customer_360 WHERE full_name = 'Ben Shah'")[0][0]
    assert ben == 2
    # Dev only exists as a DB order -> still becomes a (guest) customer with the refunded order.
    assert q("SELECT refunded_orders FROM gold.customer_360 WHERE email = 'dev@example.com'")[0][0] == 1


def test_golden_record_uses_source_priority(first_run):
    assert q("SELECT full_name, phone, city FROM gold.customer_360 WHERE email = 'asha.rao@example.com'")[0] == \
        ("Asha Rao", "9876543210", "Pune")


def test_incremental_load_appends_and_dedupes(first_run):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE orders SET state = 'refunded', updated_at = '2026-10-01 09:00:00' WHERE order_no = 'A-2'")
        db.execute("INSERT INTO orders VALUES ('A-4', 'asha.rao@example.com', '2026-10-02 10:00:00', 'completed', "
                   "700, '2026-10-02 10:00:00')")
    run(["acme"])
    loaded = q("SELECT rows_loaded, load_mode FROM meta.source_runs WHERE source_name = 'shop_orders' "
               "ORDER BY loaded_at DESC LIMIT 1")[0]
    assert loaded == (2, "incremental")            # only the changed + new rows were fetched
    assert q('SELECT count(*) FROM bronze."acme__shop_orders"')[0][0] == 5   # appended
    assert q("SELECT count(*) FROM silver.std_orders WHERE source_name = 'shop_orders'")[0][0] == 4  # de-duplicated
    assert q("SELECT status FROM silver.transactions WHERE order_id = 'A-2'")[0][0] == "refunded"  # latest version
    assert q("SELECT orders FROM gold.customer_360 WHERE email = 'asha.rao@example.com'")[0][0] == 2


def test_run_history_and_quality_checks(first_run):
    assert q("SELECT count(*) FROM meta.runs WHERE status IN ('success', 'partial')")[0][0] >= 1
    assert q("SELECT count(*) FROM meta.dq_results")[0][0] > 0
