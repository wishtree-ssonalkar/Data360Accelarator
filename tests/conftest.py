"""Test setup: an isolated config + warehouse for a small test client ("acme").

The cdp config module reads its environment at import time, so the test config directory is created
and the environment set here, before any test imports cdp.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="cdp_test_"))
CONFIG = WORK / "config"
SOURCES = WORK / "sources" / "acme"
DB_PATH = WORK / "acme_shop.sqlite"

TICKETS = [
    {"id": "T1", "requester": {"email": "Asha.Rao@Example.com"}, "created": "2026-09-01T10:00:00", "state": "solved",
     "csat": "5"},
    {"id": "T2", "requester": {"email": "ben@example.com"}, "created": "2026-09-05T11:00:00", "state": "open",
     "csat": None},
    {"id": "T3", "requester": {"email": "chen@example.com"}, "created": "2026-09-06T12:00:00", "state": "solved",
     "csat": "2"},
]


class _TicketsApi(BaseHTTPRequestHandler):
    """Fake helpdesk API: 2 tickets per page, ?page=N, Bearer auth required."""

    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            return
        page = int(self.path.split("page=")[1].split("&")[0]) if "page=" in self.path else 1
        body = json.dumps({"tickets": TICKETS[(page - 1) * 2: page * 2]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_api() -> int:
    server = HTTPServer(("127.0.0.1", 0), _TicketsApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2), encoding="utf-8")


def _build() -> None:
    shutil.copytree(ROOT / "config" / "source_types", CONFIG / "source_types")
    _write(CONFIG / "platform.json", {
        "warehouse_path": str(WORK / "test.duckdb"), "sources_root": str(WORK / "sources"),
        "as_of_date": "2026-10-15", "log_dir": str(WORK / "logs"),
        "default_business_rules": {"active_window_days": 180, "churn_risk_high_days": 240,
                                   "churn_risk_medium_days": 120, "recent_activity_days": 90,
                                   "tier_thresholds": {"Platinum": 60000, "Gold": 25000, "Silver": 8000}},
    })
    # A client-specific source type: orders straight from the shop's database.
    _write(CONFIG / "source_types" / "acme_db_orders.json", {
        "system": "Acme Shop DB", "outputs": [{
            "entity": "orders",
            "columns": {"order_id": "order_no", "email": "buyer_email", "order_ts": "CAST(placed_at AS TIMESTAMP)",
                        "channel": "'Web'", "status": "lower(state)", "order_value": "CAST(total AS DECIMAL(14,2))"},
        }],
    })
    _write(CONFIG / "source_types" / "acme_api_tickets.json", {
        "system": "Acme Helpdesk", "outputs": [{
            "entity": "tickets",
            "columns": {"ticket_id": "id", "email": "requester_email", "created_at": "CAST(created AS TIMESTAMP)",
                        "status": "state", "csat": "TRY_CAST(csat AS INTEGER)"},
        }],
    })
    port = _start_api()
    os.environ["ACME_HELPDESK_TOKEN"] = "test-token"
    os.environ["ACME_DB"] = str(DB_PATH)
    _write(CONFIG / "tenants" / "acme.json", {
        "tenant_id": "acme", "name": "Acme Test", "currency": "INR", "phone_digits": 10,
        "sources": [
            {"name": "shop_customers", "type": "shopify_customers", "priority": 1,
             "connector": {"kind": "file", "path": "customers.csv"}},
            {"name": "shop_orders", "type": "acme_db_orders",
             "connector": {"kind": "database", "engine": "sqlite", "connection_env": "ACME_DB", "table": "orders",
                           "incremental": {"column": "updated_at"}}},
            {"name": "helpdesk", "type": "acme_api_tickets",
             "connector": {"kind": "rest", "url": f"http://127.0.0.1:{port}/tickets",
                           "headers": {"Authorization": "Bearer ${ACME_HELPDESK_TOKEN}"},
                           "records_path": "tickets", "pagination": {"type": "page", "size": 2}}},
            {"name": "legacy_crm", "type": "salesforce_contacts",
             "connector": {"kind": "file", "path": "does_not_exist.csv"}},
        ],
    })
    _write(SOURCES / "customers.csv",
           "id,email,first_name,last_name,phone,default_address_city,created_at,accepts_marketing\n"
           "S1,asha.rao@example.com,Asha,Rao,+91 98765 43210,Pune,2025-01-10T10:00:00+05:30,True\n"
           "S2,BEN@example.com,Ben,Shah,,Mumbai,2025-02-11T10:00:00+05:30,False\n"
           "S3,ben.alt@example.com,Ben,Shah,098765-11111,Mumbai,2025-03-01T10:00:00+05:30,False\n")
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE orders (order_no TEXT, buyer_email TEXT, placed_at TEXT, state TEXT, total REAL, "
                   "updated_at TEXT)")
        db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", [
            ("A-1", "asha.rao@example.com", "2026-08-01 10:00:00", "COMPLETED", 5000, "2026-08-01 10:00:00"),
            ("A-2", "ben@example.com", "2026-08-03 10:00:00", "completed", 1200, "2026-08-03 10:00:00"),
            ("A-3", "dev@example.com", "2026-09-10 10:00:00", "refunded", 800, "2026-09-10 10:00:00"),
        ])


os.environ["CDP_CONFIG_DIR"] = str(CONFIG)
os.environ.pop("CDP_WAREHOUSE", None)
os.environ.pop("CDP_SOURCES_ROOT", None)
os.environ.pop("CDP_AS_OF", None)
_build()
