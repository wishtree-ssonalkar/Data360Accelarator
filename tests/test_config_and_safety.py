"""Config validation, mapping SQL generation and the AI assistant's SQL guard."""
from __future__ import annotations

import pytest

from cdp.assistant import validate_sql
from cdp.config import ROOT, validate
from cdp.mapping import output_sql


def _tenant(**overrides):
    t = {"tenant_id": "demo", "name": "Demo", "currency": "INR",
         "sources": [{"name": "crm", "type": "good_type", "connector": {"kind": "file", "path": "crm.csv"}}]}
    t.update(overrides)
    return t


GOOD_TYPE = {"system": "CRM", "outputs": [{"entity": "customers", "columns": {"source_key": "id", "email": "mail"}}]}


def test_valid_config_has_no_problems():
    assert validate([_tenant()], {"good_type": GOOD_TYPE}) == []


def test_repository_config_is_valid():
    import json
    types = {p.stem: json.loads(p.read_text()) for p in (ROOT / "config" / "source_types").glob("*.json")}
    tenants = [json.loads(p.read_text()) for p in (ROOT / "config" / "tenants").glob("*.json")]
    assert validate(tenants, types) == []


@pytest.mark.parametrize("source_type, expected", [
    ({"system": "X", "outputs": [{"entity": "clients", "columns": {}}]}, "must be one of"),
    ({"system": "X", "outputs": [{"entity": "customers", "columns": {"email": "e"}}]}, "required customers columns"),
    ({"system": "X", "outputs": [{"entity": "customers", "columns": {"source_key": "id", "shoe_size": "s"}}]},
     "unknown customers columns"),
    ({"system": "X", "outputs": [{"entity": "orders", "columns": {
        "order_id": "a", "order_ts": "b", "channel": "c", "status": "d", "customer_source_key": "e"}}]},
     "customer_namespace"),
])
def test_bad_source_types_are_reported(source_type, expected):
    problems = validate([_tenant()], {"good_type": source_type})
    assert any(expected in p for p in problems), problems


def test_bad_tenant_settings_are_reported():
    tenant = _tenant(sources=[
        {"name": "a", "type": "missing_type", "connector": {"kind": "file", "path": "x"}},
        {"name": "b", "type": "good_type", "connector": {"kind": "database", "engine": "odbc", "table": "t"}},
        {"name": "b", "type": "good_type", "connector": {"kind": "ftp"}},
    ])
    problems = "\n".join(validate([tenant], {"good_type": GOOD_TYPE}))
    assert "unknown type 'missing_type'" in problems
    assert "connection_env" in problems
    assert "duplicate source name 'b'" in problems
    assert "connector.kind" in problems


def test_mapping_sql_casts_and_fills_unmapped_columns():
    sql = output_sql(_tenant(), _tenant()["sources"][0], GOOD_TYPE, GOOD_TYPE["outputs"][0])
    assert "CAST((mail) AS VARCHAR) AS email" in sql
    assert "CAST(NULL AS VARCHAR) AS phone" in sql
    assert 'bronze."demo__crm"' in sql


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customer_360",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM customer_360 WHERE issue = 'Set up; load and copy'",
])
def test_assistant_allows_reads(sql):
    validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE customer_360",
    "SELECT 1; DELETE FROM customer_360",
    "SELECT * FROM read_csv('secrets.csv')",
    "SET enable_external_access = true",
    "SELECT 1 -- ; DROP TABLE x\n; ATTACH 'x.db'",
])
def test_assistant_blocks_writes_and_file_access(sql):
    with pytest.raises(ValueError):
        validate_sql(sql)
