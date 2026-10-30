"""Admin back-end: encrypted credentials, validated config writes with backups, previews."""
from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from cdp import config_store, secrets
from cdp.config import CONFIG_DIR, ConfigError, load_source_types, load_tenants
from cdp.connectors import preview


@pytest.fixture(autouse=True)
def secret_store(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CDP_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CDP_SECRETS_PATH", str(tmp_path / "secrets.enc"))


def test_credentials_are_encrypted_and_resolved():
    secrets.set_secret("ACME_TEST_TOKEN", "super-secret-value")
    raw = (secrets._path()).read_text()
    assert "super-secret-value" not in raw
    assert secrets.list_names() == ["ACME_TEST_TOKEN"]
    assert secrets.get_secret("ACME_TEST_TOKEN") == "super-secret-value"
    secrets.delete_secret("ACME_TEST_TOKEN")
    assert secrets.get_secret("ACME_TEST_TOKEN") is None


def test_wrong_master_key_is_reported(monkeypatch):
    from cryptography.fernet import Fernet
    secrets.set_secret("ACME_OTHER", "value")
    monkeypatch.setenv("CDP_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        secrets.get_secret("ACME_OTHER")


def test_credential_names_are_checked():
    with pytest.raises(ValueError):
        secrets.set_secret("lower-case", "x")


def test_stored_credential_feeds_a_connector(monkeypatch):
    # The test client's database source reads its path from ACME_DB; serve it from the store instead.
    path = os.environ["ACME_DB"]
    monkeypatch.delenv("ACME_DB")
    secrets.set_secret("ACME_DB", path)
    tenant = next(t for t in load_tenants() if t["tenant_id"] == "acme")
    source = next(s for s in tenant["sources"] if s["name"] == "shop_orders")
    df = preview("acme", source, load_source_types()[source["type"]], limit=2)
    assert len(df) == 2 and "order_no" in df.columns
    monkeypatch.setenv("ACME_DB", path)


def test_invalid_change_is_rejected_and_files_untouched():
    before = (CONFIG_DIR / "tenants" / "acme.json").read_text()
    bad = {"name": "x", "type": "no_such_mapping", "connector": {"kind": "file", "path": "x.csv"}}
    with pytest.raises(ConfigError, match="unknown type"):
        config_store.save_source("acme", bad)
    assert (CONFIG_DIR / "tenants" / "acme.json").read_text() == before


def test_save_backs_up_audits_and_reloads():
    config_store.save_source("acme", {"name": "extra_customers", "type": "shopify_customers",
                                      "connector": {"kind": "file", "path": "customers.csv"}}, user="tester")
    names = [s["name"] for t in load_tenants() if t["tenant_id"] == "acme" for s in t["sources"]]
    assert "extra_customers" in names                         # caches cleared, new config visible
    assert list((CONFIG_DIR / ".history").glob("*__tenants__acme.json"))  # previous version kept
    assert config_store.audit_log()[0]["user"] == "tester"
    config_store.delete_source("acme", "extra_customers")
    assert "extra_customers" not in [s["name"] for t in load_tenants() for s in t["sources"]]


def test_new_mapping_from_ui_and_preview():
    mapping = {"system": "Acme Loyalty", "outputs": [{"entity": "customers", "namespace": "loyalty", "columns": {
        "source_key": '"Member No"', "email": '"E-mail"', "full_name": "trim(\"Name\")"}}]}
    raw = pd.DataFrame({"Member No": ["M1"], "E-mail": [" A@B.COM "], "Name": [" Asha "]})
    result = config_store.mapping_preview({"tenant_id": "acme"}, {"name": "loyalty"}, mapping, raw)
    row = result["customers"].iloc[0]
    assert (row["source_key"], row["full_name"]) == ("M1", "Asha")
    config_store.save_source_type("acme_loyalty", mapping)
    assert "acme_loyalty" in load_source_types()
    assert json.loads((CONFIG_DIR / "source_types" / "acme_loyalty.json").read_text())["system"] == "Acme Loyalty"
    config_store.delete_source_type("acme_loyalty")


def test_mapping_in_use_cannot_be_deleted():
    with pytest.raises(ConfigError, match="is used by"):
        config_store.delete_source_type("shopify_customers")
