"""Generate realistic, messy source-system extracts for each tenant.

Each tenant gets the files a real retailer would hand over: Shopify customers and
orders, Salesforce contacts, store POS sales, Zendesk tickets, web/app events and a
product catalog. The same real person shows up differently in each system
(email casing, phone formats, abbreviated names, duplicate accounts, missing keys),
which is exactly what the identity-resolution step has to untangle.

Run:  python -m cdp sample-data   (only tenants with a "sample_data" block)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from cdp.config import AS_OF_DATE, SOURCES_DIR, load_tenants

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
    "Kabir", "Ananya", "Diya", "Riya", "Aadhya", "Saanvi", "Myra", "Pari", "Anika", "Navya",
    "Kiara", "Meera", "Priya", "Sneha", "Neha", "Pooja", "Rahul", "Amit", "Vikram", "Karan",
    "Siddharth", "Nikhil", "Tanvi", "Shruti", "Kavya", "Isha", "Aditi", "Varun", "Harsh", "Yash",
]
LAST_NAMES = [
    "Sharma", "Verma", "Patel", "Iyer", "Reddy", "Nair", "Gupta", "Mehta", "Joshi", "Kulkarni",
    "Desai", "Rao", "Singh", "Kapoor", "Malhotra", "Bose", "Chatterjee", "Menon", "Pillai", "Shah",
    "Deshmukh", "Patil", "Jain", "Agarwal", "Banerjee", "Sonawane", "Pawar", "Kaur", "Thakur", "Mishra",
]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi", "Hyderabad", "Chennai", "Kolkata", "Ahmedabad", "Jaipur", "Kochi"]
CITY_WEIGHTS = [0.18, 0.14, 0.15, 0.14, 0.1, 0.08, 0.06, 0.06, 0.05, 0.04]
EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com"]

CATALOGS = {
    "stride": {
        "Running Shoes": (2499, 8999), "Casual Sneakers": (1499, 5999), "Formal Shoes": (2999, 9999),
        "Sports & Training": (1999, 6999), "Sandals & Slides": (499, 2499), "Kids": (799, 2999),
        "Socks & Accessories": (199, 1499),
    },
    "urbanleaf": {
        "Lighting": (899, 7999), "Furniture": (4999, 34999), "Wall Decor": (499, 4999),
        "Kitchen & Dining": (299, 3999), "Bed & Bath": (799, 5999), "Indoor Plants": (299, 2499),
        "Storage": (599, 4999),
    },
}
PRODUCT_WORDS = {
    "stride": ["Aero", "Pulse", "Glide", "Strider", "Volt", "Trail", "Nimbus", "Urban", "Classic", "Swift"],
    "urbanleaf": ["Nordic", "Terra", "Lumen", "Oak", "Willow", "Jaipur", "Coastal", "Loft", "Sage", "Amber"],
}
TICKET_TYPES = {
    "stride": ["Size / fit issue", "Delivery delay", "Damaged item", "Refund status", "Payment issue", "Product question"],
    "urbanleaf": ["Damaged in transit", "Delivery delay", "Assembly help", "Refund status", "Payment issue", "Product question"],
}


def _rand_date(rng: np.random.Generator, start: date, end: date) -> date:
    return start + timedelta(days=int(rng.integers(0, max((end - start).days, 1))))


def _phone_variant(rng: np.random.Generator, digits: str) -> str:
    """The same 10-digit mobile number written the way different systems store it."""
    style = rng.integers(0, 5)
    return [
        f"+91 {digits[:5]} {digits[5:]}",
        f"0{digits}",
        f"+91-{digits}",
        digits,
        f"{digits[:5]}-{digits[5:]}",
    ][style]


def _email_variant(rng: np.random.Generator, email: str) -> str:
    style = rng.random()
    if style < 0.12:
        return email.upper()
    if style < 0.22:
        return f"  {email.capitalize()} "
    return email


def build_catalog(tenant_id: str, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    words = PRODUCT_WORDS[tenant_id]
    for c_idx, (category, (lo, hi)) in enumerate(CATALOGS[tenant_id].items()):
        for i in range(int(rng.integers(7, 11))):
            price = int(round(rng.uniform(lo, hi) / 50) * 50 - 1)
            rows.append({
                "sku": f"{tenant_id[:2].upper()}-{c_idx + 1:02d}{i + 1:02d}",
                "title": f"{rng.choice(words)} {category.split(' ')[0]} {['Pro', 'Lite', 'Max', 'Edge', 'One', 'Plus'][i % 6]}",
                "category": category,
                "list_price": price,
                "unit_cost": int(price * rng.uniform(0.38, 0.62)),
                "launched_on": _rand_date(rng, date(2022, 1, 1), date(2026, 3, 1)).isoformat(),
                "is_active": bool(rng.random() > 0.06),
            })
    return pd.DataFrame(rows)


def build_people(tenant: dict, rng: np.random.Generator) -> pd.DataFrame:
    n = tenant["sample_data"]["people"]
    first = rng.choice(FIRST_NAMES, n)
    last = rng.choice(LAST_NAMES, n)
    people = pd.DataFrame({
        "person_id": np.arange(n),
        "first": first,
        "last": last,
        "city": rng.choice(CITIES, n, p=CITY_WEIGHTS),
        "phone": [str(rng.integers(7, 10)) + "".join(map(str, rng.integers(0, 10, 9))) for _ in range(n)],
    })
    people["email"] = [
        f"{f.lower()}.{l.lower()}{rng.integers(1, 999) if rng.random() < 0.7 else ''}@{rng.choice(EMAIL_DOMAINS)}"
        for f, l in zip(first, last)
    ]
    people["signup"] = [_rand_date(rng, date(2023, 6, 1), AS_OF_DATE - timedelta(days=20)) for _ in range(n)]
    # Latent behaviour: how often they buy, how much, how long they stay, where they shop.
    people["orders_per_year"] = np.clip(rng.lognormal(1.0, 0.75, n), 0.4, 26)
    people["basket_mult"] = np.clip(rng.lognormal(0, 0.45, n), 0.4, 3.5)
    people["lifetime_days"] = rng.exponential(560, n).astype(int) + 30
    people["channel_pref"] = rng.choice(["web", "app", "store"], n, p=[0.48, 0.3, 0.22])
    people["never_buys"] = rng.random(n) < 0.08
    return people


def build_orders(people: pd.DataFrame, catalog: pd.DataFrame, rng: np.random.Generator):
    active_catalog = catalog[catalog["is_active"]].reset_index(drop=True)
    # A few products are "problem" products with higher refund rates.
    problem_skus = set(rng.choice(active_catalog["sku"], 5, replace=False))
    orders, items = [], []
    order_seq = 100000
    for p in people.itertuples():
        if p.never_buys:
            continue
        start = p.signup + timedelta(days=int(rng.integers(0, 45)))
        end = min(p.signup + timedelta(days=int(p.lifetime_days)), AS_OF_DATE)
        if start >= end:
            continue
        t = start
        while t <= end:
            channel = p.channel_pref if rng.random() < 0.75 else rng.choice(["web", "app", "store"])
            order_seq += 1
            n_items = int(rng.choice([1, 1, 1, 2, 2, 3]))
            lines = active_catalog.sample(n_items, random_state=int(rng.integers(0, 1_000_000)))
            has_problem = any(s in problem_skus for s in lines["sku"])
            r = rng.random()
            refund_p = 0.16 if has_problem else 0.05
            status = "refunded" if r < refund_p else ("cancelled" if r < refund_p + 0.035 else "completed")
            ts = datetime.combine(t, datetime.min.time()) + timedelta(minutes=int(rng.integers(8 * 60, 23 * 60)))
            orders.append({
                "order_seq": order_seq, "person_id": p.person_id, "ts": ts, "channel": channel, "status": status,
            })
            for ln_no, ln in enumerate(lines.itertuples(), start=1):
                discount = rng.choice([0, 0, 0, 0.1, 0.2])
                items.append({
                    "order_seq": order_seq, "line_no": ln_no, "sku": ln.sku,
                    # Big spenders buy multiples more often.
                    "qty": int(rng.choice([1, 1, 1, 2])) + int(rng.random() < (p.basket_mult - 1) / 4),
                    "unit_price": round(ln.list_price * (1 - discount), 2),
                })
            gap = rng.exponential(365 / p.orders_per_year)
            t = t + timedelta(days=max(int(gap), 1))
    return pd.DataFrame(orders), pd.DataFrame(items), problem_skus


def write_tenant(tenant: dict) -> dict:
    tenant_id = tenant["tenant_id"]
    rng = np.random.default_rng(tenant["sample_data"]["seed"])
    out = SOURCES_DIR / tenant_id
    for sub in ["shopify", "crm", "pos", "support", "web", "catalog"]:
        (out / sub).mkdir(parents=True, exist_ok=True)

    catalog = build_catalog(tenant_id, rng)
    catalog.to_csv(out / "catalog/products.csv", index=False)

    people = build_people(tenant, rng)
    orders, items, problem_skus = build_orders(people, catalog, rng)

    # ---- Shopify customers (online buyers + some registered non-buyers, with duplicate accounts)
    online_buyers = set(orders.loc[orders["channel"] != "store", "person_id"])
    shop_people = people[people["person_id"].isin(online_buyers) | (rng.random(len(people)) < 0.25)]
    shop_rows, account_of = [], {}
    sid = 5_000_000
    for p in shop_people.itertuples():
        sid += int(rng.integers(1, 9))
        account_of[p.person_id] = [f"gid://shopify/Customer/{sid}"]
        shop_rows.append({
            "id": f"gid://shopify/Customer/{sid}", "email": _email_variant(rng, p.email),
            "first_name": p.first, "last_name": p.last,
            "phone": _phone_variant(rng, p.phone) if rng.random() < 0.85 else "",
            "default_address_city": p.city,
            "created_at": f"{p.signup.isoformat()}T{int(rng.integers(0, 24)):02d}:{int(rng.integers(0, 60)):02d}:00+05:30",
            "accepts_marketing": bool(rng.random() < 0.55),
        })
        if rng.random() < 0.05:  # second account: new email, same phone
            sid += 3
            account_of[p.person_id].append(f"gid://shopify/Customer/{sid}")
            shop_rows.append({
                "id": f"gid://shopify/Customer/{sid}",
                "email": f"{p.first.lower()}{int(rng.integers(10, 99))}@{rng.choice(EMAIL_DOMAINS)}",
                "first_name": p.first, "last_name": p.last if rng.random() < 0.6 else p.last[0],
                "phone": _phone_variant(rng, p.phone), "default_address_city": p.city,
                "created_at": f"{(p.signup + timedelta(days=int(rng.integers(30, 300)))).isoformat()}T10:00:00+05:30",
                "accepts_marketing": False,
            })
    pd.DataFrame(shop_rows).to_csv(out / "shopify/customers.csv", index=False)

    online = orders[orders["channel"] != "store"].copy()
    online["order_id"] = "#" + online["order_seq"].astype(str)
    online["customer_id"] = [rng.choice(account_of[pid]) for pid in online["person_id"]]
    online["created_at"] = online["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
    online["source_name"] = online["channel"].map({"web": "web", "app": "shopify_mobile_app"})
    online["financial_status"] = online["status"].map({"completed": "paid", "refunded": "refunded", "cancelled": "voided"})
    online[["order_id", "customer_id", "created_at", "source_name", "financial_status"]].to_csv(
        out / "shopify/orders.csv", index=False)
    online_items = items.merge(online[["order_seq", "order_id"]], on="order_seq")
    online_items["line_id"] = online_items["order_id"] + "-" + online_items["line_no"].astype(str)
    online_items.rename(columns={"qty": "quantity", "unit_price": "price"})[
        ["line_id", "order_id", "sku", "quantity", "price"]].to_csv(out / "shopify/order_items.csv", index=False)

    # ---- Store POS export (matched only by phone; some walk-ins give no phone)
    store = orders[orders["channel"] == "store"].merge(items, on="order_seq").merge(
        people[["person_id", "phone"]], on="person_id")
    store["receipt_line"] = "R" + store["order_seq"].astype(str) + "/" + store["line_no"].astype(str)
    store["receipt_no"] = "R" + store["order_seq"].astype(str)
    store["customer_phone"] = [
        _phone_variant(rng, ph) if rng.random() > 0.06 else "" for ph in store["phone"]]
    store["txn_datetime"] = store["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
    store["store_code"] = rng.choice(["MUM-01", "PUN-02", "BLR-01", "DEL-03", "HYD-01"], len(store))
    store["line_amount"] = (store["qty"] * store["unit_price"]).round(2)
    store["is_return"] = store["status"] == "refunded"
    store[store["status"] != "cancelled"][[
        "receipt_line", "receipt_no", "txn_datetime", "store_code", "customer_phone", "sku", "qty", "line_amount",
        "is_return"]].to_csv(out / "pos/sales.csv", index=False)

    # ---- Salesforce CRM contacts (abbreviated names, odd phone formats, some missing emails, some prospects)
    crm_people = people[rng.random(len(people)) < 0.62]
    crm_rows = []
    for p in crm_people.itertuples():
        crm_rows.append({
            "ContactId": f"003{rng.integers(10**11, 10**12)}",
            "FullName": f"{p.first} {p.last}" if rng.random() < 0.7 else f"{p.first} {p.last[0]}.",
            "Email": _email_variant(rng, p.email) if rng.random() < 0.88 else "",
            "Phone": _phone_variant(rng, p.phone) if rng.random() < 0.8 else "",
            "MailingCity": p.city if rng.random() < 0.9 else p.city.upper(),
            "LeadSource": rng.choice(["Website", "Instagram", "Referral", "Store Walk-in", "Google Ads", "Event"]),
            "CreatedDate": (p.signup - timedelta(days=int(rng.integers(0, 60)))).strftime("%d/%m/%Y"),
            "OwnerRegion": rng.choice(["West", "South", "North", "East"]),
        })
    pd.DataFrame(crm_rows).to_csv(out / "crm/contacts.csv", index=False)

    # ---- Zendesk tickets
    per_person_orders = orders.groupby("person_id").size()
    order_lookup = orders.merge(items[items["line_no"] == 1][["order_seq", "sku"]], on="order_seq")
    ticket_rows, tid = [], 40000
    types = TICKET_TYPES[tenant_id]
    for p in people.itertuples():
        n_orders = int(per_person_orders.get(p.person_id, 0))
        n_t = rng.poisson(0.12 * n_orders + 0.05)
        p_orders = order_lookup[order_lookup["person_id"] == p.person_id]
        for _ in range(n_t):
            tid += 1
            linked = p_orders.sample(1, random_state=int(rng.integers(0, 10**6))).iloc[0] if len(p_orders) else None
            created = (linked["ts"] + timedelta(days=int(rng.integers(1, 12)))) if linked is not None else \
                datetime.combine(_rand_date(rng, p.signup, AS_OF_DATE), datetime.min.time())
            if created.date() > AS_OF_DATE:
                continue
            is_problem = linked is not None and linked["sku"] in problem_skus
            ttype = types[0] if is_problem and rng.random() < 0.6 else rng.choice(types)
            solved = (AS_OF_DATE - created.date()).days > 10 or rng.random() < 0.5
            ticket_rows.append({
                "ticket_id": tid, "requester_email": _email_variant(rng, p.email),
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "subject": ttype,
                "status": "solved" if solved else rng.choice(["open", "pending"]),
                "priority": rng.choice(["low", "normal", "normal", "high"]),
                "satisfaction_score": int(np.clip(rng.normal(2.4 if is_problem else 4.1, 1.0), 1, 5)) if solved and rng.random() < 0.7 else "",
                "related_sku": linked["sku"] if linked is not None and rng.random() < 0.8 else "",
                "channel": rng.choice(["email", "chat", "phone", "whatsapp"]),
            })
    pd.DataFrame(ticket_rows).to_csv(out / "support/tickets.csv", index=False)

    # ---- Web / app events: purchase sessions for every online order + browse-only sessions
    skus = catalog["sku"].tolist()
    eid = 0
    with open(out / "web/events.jsonl", "w", encoding="utf-8") as fh:
        def emit(email, anon, device, ts, etype, sku=None, order_id=None):
            nonlocal eid
            eid += 1
            fh.write(json.dumps({
                "event_id": f"evt_{eid:08d}", "event_type": etype, "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "user_email": email, "anonymous_id": anon, "device": device, "sku": sku, "order_id": order_id,
            }) + "\n")

        def session(email, anon, device, start, depth, order_id=None, sku=None):
            ts = start
            sku = sku or rng.choice(skus)
            emit(email, anon, device, ts, "page_view")
            steps = ["product_view", "add_to_cart", "checkout_start", "purchase"][:depth]
            for s in steps:
                ts += timedelta(seconds=int(rng.integers(20, 400)))
                emit(email, anon, device, ts, s, sku=sku, order_id=order_id if s == "purchase" else None)

        online_first_sku = online.merge(items[items["line_no"] == 1][["order_seq", "sku"]], on="order_seq")
        email_of = people.set_index("person_id")["email"]
        for o in online_first_sku.itertuples():
            device = "mobile_app" if o.channel == "app" else rng.choice(["desktop", "mobile_web"])
            session(email_of[o.person_id], f"anon_{rng.integers(10**8)}", device,
                    o.ts - timedelta(minutes=int(rng.integers(5, 40))), 4, order_id=o.order_id, sku=o.sku)
        for p in people.itertuples():
            end = min(p.signup + timedelta(days=int(p.lifetime_days) + 60), AS_OF_DATE)
            for _ in range(rng.poisson(5 + p.orders_per_year)):
                depth = int(rng.choice([0, 1, 1, 1, 2, 2, 3], p=[0.32, 0.2, 0.15, 0.1, 0.1, 0.07, 0.06]))
                logged_in = rng.random() < 0.55
                start = datetime.combine(_rand_date(rng, p.signup, end), datetime.min.time()) + \
                    timedelta(minutes=int(rng.integers(0, 1440)))
                session(p.email if logged_in else None, f"anon_{rng.integers(10**8)}",
                        rng.choice(["desktop", "mobile_web", "mobile_app"]), start, depth)
        for _ in range(int(len(people) * 2.5)):  # never-identified visitors
            start = datetime.combine(_rand_date(rng, date(2023, 6, 1), AS_OF_DATE), datetime.min.time()) + \
                timedelta(minutes=int(rng.integers(0, 1440)))
            session(None, f"anon_{rng.integers(10**8)}", rng.choice(["desktop", "mobile_web"]), start,
                    int(rng.choice([0, 1, 2], p=[0.6, 0.3, 0.1])))

    return {
        "tenant": tenant_id, "people": len(people), "orders": len(orders), "order_lines": len(items),
        "shopify_customers": len(shop_rows), "crm_contacts": len(crm_rows), "tickets": len(ticket_rows),
        "web_events": eid,
    }


def main() -> None:
    for tenant in [t for t in load_tenants() if t.get("sample_data")]:
        print(write_tenant(tenant))


if __name__ == "__main__":
    main()
