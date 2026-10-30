"""The standard (canonical) customer data model.

Every source, whatever the system, is mapped into these entities. Everything downstream
(identity resolution, Customer 360, analytics, the AI assistant) only reads these, which is
why onboarding a new client or system never requires changes past the mapping layer.

Each column: (DuckDB type, required, description). Required columns must be mapped by every
source that produces the entity.
"""
from __future__ import annotations

ENTITIES: dict[str, dict[str, tuple[str, bool, str]]] = {
    "customers": {
        "source_key": ("VARCHAR", True, "Customer id in the source system"),
        "email": ("VARCHAR", False, "Email address (any format; normalized later)"),
        "phone": ("VARCHAR", False, "Phone number (any format; normalized later)"),
        "first_name": ("VARCHAR", False, "First name"),
        "last_name": ("VARCHAR", False, "Last name"),
        "full_name": ("VARCHAR", False, "Full name, if the source has only one name field"),
        "city": ("VARCHAR", False, "City"),
        "region": ("VARCHAR", False, "Region / state / sales territory"),
        "lead_source": ("VARCHAR", False, "Acquisition source"),
        "accepts_marketing": ("BOOLEAN", False, "Marketing consent"),
        "created_at": ("TIMESTAMP", False, "When the record was created in the source"),
    },
    "orders": {
        "order_id": ("VARCHAR", True, "Order / receipt id"),
        "customer_source_key": ("VARCHAR", False, "Customer id in the system named by customer_namespace"),
        "email": ("VARCHAR", False, "Buyer email if the order carries it"),
        "phone": ("VARCHAR", False, "Buyer phone if the order carries it"),
        "order_ts": ("TIMESTAMP", True, "Order date-time"),
        "channel": ("VARCHAR", True, "Web, App, Store, Marketplace, ..."),
        "store_code": ("VARCHAR", False, "Store / location for offline orders"),
        "status": ("VARCHAR", True, "completed, refunded or cancelled"),
        "order_value": ("DECIMAL(14,2)", False, "Order total; used when the source has no order lines"),
    },
    "order_lines": {
        "line_id": ("VARCHAR", True, "Unique line id"),
        "order_id": ("VARCHAR", True, "Order the line belongs to"),
        "sku": ("VARCHAR", False, "Product code"),
        "quantity": ("INTEGER", True, "Units"),
        "line_amount": ("DECIMAL(14,2)", True, "Line total after discounts"),
    },
    "products": {
        "sku": ("VARCHAR", True, "Product code"),
        "product_name": ("VARCHAR", True, "Product name"),
        "category": ("VARCHAR", False, "Category"),
        "list_price": ("DECIMAL(14,2)", False, "List price"),
        "unit_cost": ("DECIMAL(14,2)", False, "Unit cost (for margin)"),
        "launched_on": ("DATE", False, "Launch date"),
        "is_active": ("BOOLEAN", False, "Currently sold"),
    },
    "events": {
        "event_id": ("VARCHAR", True, "Event id"),
        "event_type": ("VARCHAR", True, "page_view, product_view, add_to_cart, checkout_start, purchase, ..."),
        "event_ts": ("TIMESTAMP", True, "Event time"),
        "email": ("VARCHAR", False, "Logged-in user email"),
        "phone": ("VARCHAR", False, "Logged-in user phone"),
        "customer_source_key": ("VARCHAR", False, "Logged-in user id in customer_namespace"),
        "session_id": ("VARCHAR", True, "Browsing session"),
        "device": ("VARCHAR", False, "desktop, mobile_web, mobile_app, ..."),
        "sku": ("VARCHAR", False, "Product involved"),
        "order_id": ("VARCHAR", False, "Order created by the event"),
    },
    "tickets": {
        "ticket_id": ("VARCHAR", True, "Ticket id"),
        "email": ("VARCHAR", False, "Requester email"),
        "phone": ("VARCHAR", False, "Requester phone"),
        "customer_source_key": ("VARCHAR", False, "Requester id in customer_namespace"),
        "created_at": ("TIMESTAMP", True, "Created time"),
        "issue_type": ("VARCHAR", False, "Subject / category"),
        "status": ("VARCHAR", True, "Status; 'solved' or 'closed' means resolved"),
        "priority": ("VARCHAR", False, "Priority"),
        "csat": ("INTEGER", False, "Satisfaction score 1-5"),
        "sku": ("VARCHAR", False, "Related product"),
        "channel": ("VARCHAR", False, "email, chat, phone, ..."),
    },
}

# Primary key of each entity (unique per tenant + source); used to de-duplicate incremental loads.
PRIMARY_KEYS = {
    "customers": "source_key", "orders": "order_id", "order_lines": "line_id", "products": "sku",
    "events": "event_id", "tickets": "ticket_id",
}

# Entities whose rows can be tied to a person, and therefore feed identity resolution.
IDENTITY_ENTITIES = ("customers", "orders", "events", "tickets")

ORDER_STATUSES = ("completed", "refunded", "cancelled")
