"""Configuration: platform settings, tenants (one file per client) and source types (one file per system).

Layout
  config/platform.json             warehouse location, as-of date, default business rules
  config/tenants/<tenant>.json     one client: name, rule overrides, and its list of sources + connectors
  config/source_types/<type>.json  one kind of source system: how to read it and map it to the standard model

Environment overrides: CDP_WAREHOUSE, CDP_SOURCES_ROOT, CDP_AS_OF ("today" or YYYY-MM-DD),
CDP_LOG_DIR, CDP_CONFIG_DIR.
"""
from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from cdp.model import ENTITIES

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("CDP_CONFIG_DIR", ROOT / "config"))
SQL_DIR = ROOT / "sql"
DATA_DIR = ROOT / "data"

CONNECTOR_KINDS = {"file", "database", "rest"}
FILE_FORMATS = {"csv", "jsonl", "json", "parquet", "excel"}


class ConfigError(ValueError):
    """Raised with every problem found, so a whole config can be fixed in one pass."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Invalid configuration:\n  - " + "\n  - ".join(problems))


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{path.name}: not valid JSON ({exc})"]) from exc


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT / path


@lru_cache
def platform() -> dict:
    return _read(CONFIG_DIR / "platform.json")


def brand() -> dict:
    """Product name shown in the app, CLI and AI prompts. Change it in config/platform.json -> branding."""
    defaults = {"product_name": "WishUnify", "tagline": "Customer Data Platform accelerator",
                "company": "Wishtree Technologies"}
    return {**defaults, **platform().get("branding", {})}


WAREHOUSE_PATH = _resolve(os.environ.get("CDP_WAREHOUSE", platform()["warehouse_path"]))
SOURCES_DIR = _resolve(os.environ.get("CDP_SOURCES_ROOT", platform()["sources_root"]))
LOG_DIR = _resolve(os.environ.get("CDP_LOG_DIR", platform().get("log_dir", "data/logs")))


def _as_of() -> date:
    value = os.environ.get("CDP_AS_OF", platform().get("as_of_date") or "today")
    return date.today() if value == "today" else date.fromisoformat(value)


# "Today" for all recency metrics. Pinned for the sample data; set to "today" in production.
AS_OF_DATE = _as_of()


@lru_cache
def load_source_types() -> dict[str, dict]:
    return {p.stem: _read(p) for p in sorted((CONFIG_DIR / "source_types").glob("*.json"))}


@lru_cache
def load_tenants() -> list[dict]:
    tenants = [_read(p) for p in sorted((CONFIG_DIR / "tenants").glob("*.json"))]
    problems = validate(tenants, load_source_types())
    if problems:
        raise ConfigError(problems)
    return tenants


def tenant_config(tenant_id: str) -> dict:
    for t in load_tenants():
        if t["tenant_id"] == tenant_id:
            return t
    raise KeyError(f"Unknown tenant '{tenant_id}'")


def business_rules(tenant_id: str) -> dict:
    rules = json.loads(json.dumps(platform()["default_business_rules"]))
    overrides = tenant_config(tenant_id).get("business_rules", {})
    for key, value in overrides.items():
        if isinstance(value, dict):
            rules.setdefault(key, {}).update(value)
        else:
            rules[key] = value
    return rules


def validate(tenants: list[dict], source_types: dict[str, dict]) -> list[str]:
    """Return a list of human-readable problems (empty when the configuration is valid)."""
    problems: list[str] = []

    for type_name, st in source_types.items():
        where = f"source_types/{type_name}.json"
        if "system" not in st:
            problems.append(f"{where}: missing 'system'")
        fmt = st.get("read", {}).get("format")
        if fmt and fmt not in FILE_FORMATS:
            problems.append(f"{where}: read.format '{fmt}' must be one of {sorted(FILE_FORMATS)}")
        if not st.get("outputs"):
            problems.append(f"{where}: needs at least one entry in 'outputs'")
        for i, out in enumerate(st.get("outputs", [])):
            entity = out.get("entity")
            if entity not in ENTITIES:
                problems.append(f"{where}: outputs[{i}].entity '{entity}' must be one of {sorted(ENTITIES)}")
                continue
            columns = out.get("columns", {})
            unknown = sorted(set(columns) - set(ENTITIES[entity]))
            if unknown:
                problems.append(f"{where}: outputs[{i}] maps unknown {entity} columns {unknown}")
            missing = sorted(c for c, (_, req, _) in ENTITIES[entity].items() if req and c not in columns)
            if missing:
                problems.append(f"{where}: outputs[{i}] must map required {entity} columns {missing}")
            if "customer_source_key" in columns and not out.get("customer_namespace"):
                problems.append(f"{where}: outputs[{i}] maps customer_source_key but has no 'customer_namespace'")

    seen_ids = set()
    for t in tenants:
        tid = t.get("tenant_id", "?")
        where = f"tenants/{tid}.json"
        for key in ("tenant_id", "name", "currency", "sources"):
            if key not in t:
                problems.append(f"{where}: missing '{key}'")
        if not str(tid).replace("_", "").isalnum() or str(tid) != str(tid).lower():
            problems.append(f"{where}: tenant_id must be lowercase letters, digits or underscores")
        if tid in seen_ids:
            problems.append(f"{where}: duplicate tenant_id '{tid}'")
        seen_ids.add(tid)
        names = set()
        for i, src in enumerate(t.get("sources", [])):
            name = src.get("name", f"#{i}")
            if not str(name).replace("_", "").isalnum():
                problems.append(f"{where}: source name '{name}' must be letters, digits or underscores")
            if name in names:
                problems.append(f"{where}: duplicate source name '{name}'")
            names.add(name)
            if src.get("type") not in source_types:
                problems.append(f"{where}: source '{name}' has unknown type '{src.get('type')}' "
                                f"(add config/source_types/{src.get('type')}.json)")
            conn = src.get("connector", {})
            kind = conn.get("kind")
            if kind not in CONNECTOR_KINDS:
                problems.append(f"{where}: source '{name}' connector.kind must be one of {sorted(CONNECTOR_KINDS)}")
            elif kind == "file" and not conn.get("path"):
                problems.append(f"{where}: source '{name}' file connector needs 'path'")
            elif kind == "database":
                if not conn.get("engine"):
                    problems.append(f"{where}: source '{name}' database connector needs 'engine'")
                if not (conn.get("table") or conn.get("query")):
                    problems.append(f"{where}: source '{name}' database connector needs 'table' or 'query'")
                if not conn.get("connection_env"):
                    problems.append(f"{where}: source '{name}' database connector needs 'connection_env' "
                                    "(name of the environment variable holding the connection string)")
            elif kind == "rest" and not conn.get("url"):
                problems.append(f"{where}: source '{name}' rest connector needs 'url'")
    return problems
