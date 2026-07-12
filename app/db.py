"""SQLite data layer for Mayhem Bingo tickets (Python stdlib only)."""
import os
import sqlite3
import time
from contextlib import contextmanager

from tokens import new_id, ticket_code

DB_PATH = os.environ.get(
    "TICKETFLOW_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ticketflow.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    venue       TEXT NOT NULL DEFAULT '',
    starts_at   INTEGER NOT NULL,           -- unix seconds
    image_url   TEXT NOT NULL DEFAULT '',
    currency    TEXT NOT NULL DEFAULT 'GBP',
    published   INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_types (
    id        TEXT PRIMARY KEY,
    event_id  TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    price     INTEGER NOT NULL,             -- minor units (pence)
    quantity  INTEGER NOT NULL,             -- total available
    sold      INTEGER NOT NULL DEFAULT 0,
    sort      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    buyer_name   TEXT NOT NULL,
    buyer_email  TEXT NOT NULL,
    total        INTEGER NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'GBP',
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending|paid|cancelled
    provider     TEXT NOT NULL DEFAULT 'mock',      -- mock|stripe
    session_id   TEXT,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id             TEXT PRIMARY KEY,
    order_id       TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    ticket_type_id TEXT NOT NULL REFERENCES ticket_types(id),
    qty            INTEGER NOT NULL,
    unit_price     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id             TEXT PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    order_id       TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    ticket_type_id TEXT NOT NULL REFERENCES ticket_types(id),
    event_id       TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'valid',     -- valid|used
    scanned_at     INTEGER,
    created_at     INTEGER NOT NULL
);
"""


def _migrate(conn):
    """Additive migrations, safe to run on every boot."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    if "emailed_at" not in cols:
        # When the buyer's ticket email was sent (null = not sent yet). Lets the
        # success page be refreshed without spamming them with duplicate emails.
        conn.execute("ALTER TABLE orders ADD COLUMN emailed_at INTEGER")

    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "image" not in ecols:
        # A real cover image (uploaded file path or external URL). The original
        # `image_url` column was misnamed — it only ever held a hex accent colour.
        conn.execute("ALTER TABLE events ADD COLUMN image TEXT NOT NULL DEFAULT ''")
    if "address" not in ecols:
        # Full venue address, so punters can get directions. `venue` is just the
        # name ("The Social Club"); this is the postal address.
        conn.execute("ALTER TABLE events ADD COLUMN address TEXT NOT NULL DEFAULT ''")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with cursor() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def claim_email_send(oid) -> bool:
    """Atomically claim the right to send this order's ticket email.

    Returns True for exactly one caller; False if it's already been sent. This
    stops a refreshed success page emailing the buyer twice.
    """
    with cursor() as conn:
        cur = conn.execute(
            "UPDATE orders SET emailed_at = ? WHERE id = ? AND emailed_at IS NULL",
            (now(), oid),
        )
        return cur.rowcount == 1


def mark_email_unsent(oid):
    """Release the claim if sending actually failed, so it can be retried."""
    with cursor() as conn:
        conn.execute("UPDATE orders SET emailed_at = NULL WHERE id = ?", (oid,))


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def create_event(title, description, venue, starts_at, image_url="",
                 currency="GBP", published=True):
    eid = new_id("evt")
    with cursor() as conn:
        conn.execute(
            "INSERT INTO events (id,title,description,venue,starts_at,image_url,"
            "currency,published,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, title, description, venue, int(starts_at), image_url,
             currency, 1 if published else 0, now()),
        )
    return eid


def update_event(eid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with cursor() as conn:
        conn.execute(f"UPDATE events SET {cols} WHERE id = ?",
                     (*fields.values(), eid))


def get_event(eid):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    return dict(row) if row else None


def list_events(only_published=False, include_past=True):
    q = "SELECT * FROM events"
    conds = []
    if only_published:
        conds.append("published = 1")
    if not include_past:
        conds.append(f"starts_at >= {now()}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY starts_at ASC"
    with cursor() as conn:
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Ticket types
# ---------------------------------------------------------------------------
def add_ticket_type(event_id, name, price, quantity, sort=0):
    tid = new_id("tt")
    with cursor() as conn:
        conn.execute(
            "INSERT INTO ticket_types (id,event_id,name,price,quantity,sort) "
            "VALUES (?,?,?,?,?,?)",
            (tid, event_id, name, int(price), int(quantity), int(sort)),
        )
    return tid


def list_ticket_types(event_id):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM ticket_types WHERE event_id = ? ORDER BY sort, name",
            (event_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_ticket_type(tid):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM ticket_types WHERE id = ?",
                           (tid,)).fetchone()
    return dict(row) if row else None


def delete_ticket_type(tid):
    with cursor() as conn:
        conn.execute("DELETE FROM ticket_types WHERE id = ?", (tid,))


# ---------------------------------------------------------------------------
# Orders & tickets
# ---------------------------------------------------------------------------
def create_order(event_id, buyer_name, buyer_email, items, provider="mock",
                 currency="GBP"):
    """items: list of (ticket_type_id, qty). Validates stock. Returns order id.

    Raises ValueError if a ticket type is sold out / lacks stock.
    """
    oid = new_id("ord")
    with cursor() as conn:
        total = 0
        resolved = []
        for tt_id, qty in items:
            if qty <= 0:
                continue
            tt = conn.execute("SELECT * FROM ticket_types WHERE id = ?",
                              (tt_id,)).fetchone()
            if tt is None or tt["event_id"] != event_id:
                raise ValueError("Unknown ticket type")
            remaining = tt["quantity"] - tt["sold"]
            if qty > remaining:
                raise ValueError(f"Only {remaining} left for {tt['name']}")
            total += tt["price"] * qty
            resolved.append((tt_id, qty, tt["price"]))
        if not resolved:
            raise ValueError("No tickets selected")
        conn.execute(
            "INSERT INTO orders (id,event_id,buyer_name,buyer_email,total,"
            "currency,status,provider,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (oid, event_id, buyer_name, buyer_email, total, currency,
             "pending", provider, now()),
        )
        for tt_id, qty, price in resolved:
            conn.execute(
                "INSERT INTO order_items (id,order_id,ticket_type_id,qty,unit_price)"
                " VALUES (?,?,?,?,?)",
                (new_id("oi"), oid, tt_id, qty, price),
            )
    return oid


def get_order(oid):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    return dict(row) if row else None


def set_order_session(oid, session_id, provider):
    with cursor() as conn:
        conn.execute("UPDATE orders SET session_id = ?, provider = ? WHERE id = ?",
                     (session_id, provider, oid))


def order_items(oid):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT oi.*, tt.name AS ticket_name FROM order_items oi "
            "JOIN ticket_types tt ON tt.id = oi.ticket_type_id "
            "WHERE oi.order_id = ?", (oid,)).fetchall()
    return [dict(r) for r in rows]


def mark_order_paid(oid):
    """Idempotently mark an order paid, decrement stock, and issue tickets.

    Returns the list of ticket rows for the order (existing or newly created).
    """
    with cursor() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
        if order is None:
            raise ValueError("Unknown order")
        existing = conn.execute("SELECT * FROM tickets WHERE order_id = ?",
                                (oid,)).fetchall()
        if order["status"] == "paid" and existing:
            return [dict(r) for r in existing]

        conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (oid,))
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?",
                             (oid,)).fetchall()
        created = []
        for it in items:
            # decrement stock
            conn.execute("UPDATE ticket_types SET sold = sold + ? WHERE id = ?",
                         (it["qty"], it["ticket_type_id"]))
            for _ in range(it["qty"]):
                code = ticket_code()
                tkt_id = new_id("tkt")
                conn.execute(
                    "INSERT INTO tickets (id,code,order_id,ticket_type_id,"
                    "event_id,status,created_at) VALUES (?,?,?,?,?,?,?)",
                    (tkt_id, code, oid, it["ticket_type_id"],
                     order["event_id"], "valid", now()),
                )
                created.append(conn.execute("SELECT * FROM tickets WHERE id = ?",
                                            (tkt_id,)).fetchone())
        return [dict(r) for r in created]


def order_ticket_state(order_id):
    """Every ticket on an order, with who bought it — so the door can see
    'this is a group of 4, 1 already in, 3 to come'."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT t.code, t.status, t.scanned_at, tt.name AS ticket_name, "
            "o.buyer_name, o.buyer_email "
            "FROM tickets t "
            "JOIN ticket_types tt ON tt.id = t.ticket_type_id "
            "JOIN orders o ON o.id = t.order_id "
            "WHERE t.order_id = ? ORDER BY t.created_at", (order_id,)).fetchall()
    return [dict(r) for r in rows]


def admit_order(order_id):
    """Admit every still-valid ticket on an order in one go (group arriving
    together). Returns (admitted_count, already_count)."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT id, status FROM tickets WHERE order_id = ?", (order_id,)).fetchall()
        admitted = 0
        already = 0
        for r in rows:
            if r["status"] == "used":
                already += 1
            else:
                conn.execute(
                    "UPDATE tickets SET status = 'used', scanned_at = ? WHERE id = ?",
                    (now(), r["id"]))
                admitted += 1
    return admitted, already


def event_attendance(event_id):
    """Who's in and who's still to come, grouped by order (a booking party)."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT o.id AS order_id, o.buyer_name, o.buyer_email, o.created_at, "
            "t.code, t.status, t.scanned_at, tt.name AS ticket_name "
            "FROM tickets t "
            "JOIN orders o ON o.id = t.order_id "
            "JOIN ticket_types tt ON tt.id = t.ticket_type_id "
            "WHERE t.event_id = ? AND o.status = 'paid' "
            "ORDER BY o.buyer_name COLLATE NOCASE, t.created_at", (event_id,)).fetchall()

    parties = {}
    for r in rows:
        p = parties.setdefault(r["order_id"], {
            "order_id": r["order_id"],
            "buyer_name": r["buyer_name"],
            "buyer_email": r["buyer_email"],
            "tickets": [],
        })
        p["tickets"].append({
            "code": r["code"], "status": r["status"],
            "scanned_at": r["scanned_at"], "ticket_name": r["ticket_name"],
        })

    out = []
    for p in parties.values():
        used = sum(1 for t in p["tickets"] if t["status"] == "used")
        total = len(p["tickets"])
        p["in_count"] = used
        p["total"] = total
        p["state"] = "in" if used == total else ("partial" if used else "waiting")
        out.append(p)
    # Still-to-come first — that's what you're looking for on the night.
    order = {"waiting": 0, "partial": 1, "in": 2}
    out.sort(key=lambda p: (order[p["state"]], (p["buyer_name"] or "").lower()))
    return out


def tickets_for_order(oid):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT t.*, tt.name AS ticket_name, e.title AS event_title, "
            "e.starts_at AS event_starts_at, e.venue AS event_venue, "
            "e.address AS event_address "
            "FROM tickets t "
            "JOIN ticket_types tt ON tt.id = t.ticket_type_id "
            "JOIN events e ON e.id = t.event_id "
            "WHERE t.order_id = ? ORDER BY t.created_at", (oid,)).fetchall()
    return [dict(r) for r in rows]


def get_ticket_by_code(code):
    with cursor() as conn:
        row = conn.execute(
            "SELECT t.*, tt.name AS ticket_name, tt.price AS price, "
            "e.title AS event_title, e.starts_at AS event_starts_at, "
            "e.venue AS event_venue, e.address AS event_address, "
            "o.buyer_name AS buyer_name "
            "FROM tickets t "
            "JOIN ticket_types tt ON tt.id = t.ticket_type_id "
            "JOIN events e ON e.id = t.event_id "
            "JOIN orders o ON o.id = t.order_id "
            "WHERE t.code = ?", (code,)).fetchone()
    return dict(row) if row else None


def redeem_ticket(code):
    """Attempt to scan a ticket in. Returns (status, ticket_dict).

    status is one of: 'ok' (just admitted), 'already' (previously used),
    'invalid' (no such ticket).
    """
    with cursor() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE code = ?", (code,)).fetchone()
        if row is None:
            return "invalid", None
        if row["status"] == "used":
            full = get_ticket_by_code(code)
            return "already", full
        conn.execute("UPDATE tickets SET status = 'used', scanned_at = ? WHERE id = ?",
                     (now(), row["id"]))
    return "ok", get_ticket_by_code(code)


# ---------------------------------------------------------------------------
# Reporting for the organiser dashboard
# ---------------------------------------------------------------------------
def event_stats(event_id):
    with cursor() as conn:
        sold = conn.execute(
            "SELECT COALESCE(SUM(qty),0) AS n FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "WHERE o.event_id = ? AND o.status = 'paid'", (event_id,)).fetchone()["n"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS r FROM orders "
            "WHERE event_id = ? AND status = 'paid'", (event_id,)).fetchone()["r"]
        scanned = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets "
            "WHERE event_id = ? AND status = 'used'", (event_id,)).fetchone()["n"]
        capacity = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) AS n FROM ticket_types "
            "WHERE event_id = ?", (event_id,)).fetchone()["n"]
    return {"sold": sold, "revenue": revenue, "scanned": scanned,
            "capacity": capacity}


def list_orders(event_id, status="paid", limit=200):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE event_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT ?", (event_id, status, limit)).fetchall()
    return [dict(r) for r in rows]
