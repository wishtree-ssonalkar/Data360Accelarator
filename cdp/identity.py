"""Identity resolution: decide which records belong to the same real customer.

Works on the standard model only, so it never needs changes for new sources. Every person-related row
(customers, orders, events, tickets) carries an `identity_key`:
  * a customer record:              "<namespace>:<id>"        e.g. "shopify:123"
  * a row pointing at a customer:   the same "<namespace>:<id>" (e.g. an order's customer id)
  * a row with only contact data:   "email:<email>" or "phone:<phone>"
Each distinct key is a node. Nodes sharing a normalized email or phone are linked, and every connected
group becomes one customer. Deterministic and explainable, which is what teams need to trust the 360.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

import duckdb
import pandas as pd

from cdp.model import IDENTITY_ENTITIES


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _node_records(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    selects = [
        f"SELECT tenant_id, source_name, source_system, identity_key, email_norm, phone_norm FROM silver.std_{e} "
        "WHERE identity_key IS NOT NULL"
        for e in IDENTITY_ENTITIES
    ]
    return con.execute(f"""
        SELECT tenant_id, source_name, source_system, identity_key,
               any_value(email_norm) FILTER (WHERE email_norm IS NOT NULL) AS email_norm,
               any_value(phone_norm) FILTER (WHERE phone_norm IS NOT NULL) AS phone_norm
        FROM ({' UNION ALL '.join(selects)})
        GROUP BY ALL
    """).df()


def resolve_identities(con: duckdb.DuckDBPyConnection) -> dict:
    records = _node_records(con)
    uf = _UnionFind()
    owner: dict = {}  # (tenant, kind, value) -> first node seen with that email/phone
    for row in records.itertuples():
        node = (row.tenant_id, row.identity_key)
        uf.find(node)
        for kind, value in (("email", row.email_norm), ("phone", row.phone_norm)):
            if isinstance(value, str) and value:
                key = (row.tenant_id, kind, value)
                if key in owner:
                    uf.union(node, owner[key])
                else:
                    owner[key] = node

    roots = [uf.find((r.tenant_id, r.identity_key)) for r in records.itertuples()]
    members: dict = defaultdict(set)
    emails: dict = defaultdict(set)
    phones: dict = defaultdict(set)
    for root, row in zip(roots, records.itertuples()):
        members[root].add(row.identity_key)
        if isinstance(row.email_norm, str):
            emails[root].add(row.email_norm)
        if isinstance(row.phone_norm, str):
            phones[root].add(row.phone_norm)

    def match_rule(root) -> str:
        if len(members[root]) == 1:
            return "single source"
        rules = [r for r, present in (("email", emails[root]), ("phone", phones[root])) if present]
        if len(emails[root]) > 1:
            rules.append("multiple emails")
        return " + ".join(rules) or "shared id"

    records["customer_id"] = ["C" + hashlib.sha1(f"{r[0]}|{r[1]}".encode()).hexdigest()[:9].upper() for r in roots]
    records["match_rule"] = [match_rule(r) for r in roots]
    identity_map = records.rename(columns={"source_name": "source", "identity_key": "source_key"})  # noqa: F841
    con.execute("CREATE OR REPLACE TABLE silver.identity_map AS SELECT tenant_id, source, source_system, "
                "source_key, email_norm, phone_norm, customer_id, match_rule FROM identity_map")
    return {
        "identity_records": len(records),
        "customers": int(records["customer_id"].nunique()),
        "merged_profiles": int(sum(1 for m in members.values() if len(m) > 1)),
    }
