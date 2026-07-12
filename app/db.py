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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS discounts (
      id           TEXT PRIMARY KEY,
      code         TEXT NOT NULL UNIQUE,   -- stored UPPERCASE, matched case-insensitively
      kind         TEXT NOT NULL,          -- 'percent' | 'fixed'
      value        INTEGER NOT NULL,       -- percent: 1-100. fixed: pence off.
      event_id     TEXT,                   -- NULL = valid on every event
      max_uses     INTEGER,                -- NULL = unlimited
      used_count   INTEGER NOT NULL DEFAULT 0,
      expires_at   INTEGER,                -- NULL = never expires
      active       INTEGER NOT NULL DEFAULT 1,
      created_at   INTEGER NOT NULL
    )""")

    # Which order used which code. Also how we count redemptions reliably —
    # used_count alone could drift if an order is created but never paid.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS discount_uses (
      id           TEXT PRIMARY KEY,
      discount_id  TEXT NOT NULL,
      order_id     TEXT NOT NULL,
      amount_off   INTEGER NOT NULL,       -- pence actually taken off
      created_at   INTEGER NOT NULL
    )""")

    # Price tiers: a ticket type can change price by DATE ("early bird until 1 Aug")
    # or by QUANTITY ("first 50 at £6"). Both can apply; see effective_price().
    conn.execute("""
    CREATE TABLE IF NOT EXISTS price_tiers (
      id             TEXT PRIMARY KEY,
      ticket_type_id TEXT NOT NULL,
      name           TEXT NOT NULL,        -- "Early bird", "Tier 1"...
      price          INTEGER NOT NULL,     -- pence
      until_date     INTEGER,              -- valid while now() < this. NULL = no date rule
      max_qty        INTEGER,              -- valid while sold < this.  NULL = no qty rule
      sort_order     INTEGER NOT NULL DEFAULT 0,
      created_at     INTEGER NOT NULL
    )""")

    # Site settings (booking fee, etc). One row, keyed by name.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
      key    TEXT PRIMARY KEY,
      value  TEXT NOT NULL
    )""")

    # Add-ons sold alongside tickets — dabbers, drinks vouchers, raffle strips.
    # GLOBAL: one catalogue, one stock level, offered at every event. A box of 40
    # dabbers is one box — selling them across three nights draws from the same box.
    # Handed over on the night, so they MUST appear on the door list.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS products (
      id          TEXT PRIMARY KEY,
      name        TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      price       INTEGER NOT NULL,        -- pence
      quantity    INTEGER,                 -- master stock. NULL = unlimited.
      sold        INTEGER NOT NULL DEFAULT 0,
      max_each    INTEGER NOT NULL DEFAULT 10,  -- cap per booking
      active      INTEGER NOT NULL DEFAULT 1,
      sort_order  INTEGER NOT NULL DEFAULT 0,
      created_at  INTEGER NOT NULL
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS order_products (
      id          TEXT PRIMARY KEY,
      order_id    TEXT NOT NULL,
      product_id  TEXT NOT NULL,
      qty         INTEGER NOT NULL,
      unit_price  INTEGER NOT NULL,        -- price at time of purchase
      collected   INTEGER NOT NULL DEFAULT 0  -- ticked off at the door
    )""")

    # Migrate an older per-event products table to the global one. Earlier builds
    # scoped products to a single event; stock is now one master figure.
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    if "event_id" in pcols:
        conn.execute("ALTER TABLE products RENAME TO products_old")
        conn.execute("""
        CREATE TABLE products (
          id          TEXT PRIMARY KEY,
          name        TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          price       INTEGER NOT NULL,
          quantity    INTEGER,
          sold        INTEGER NOT NULL DEFAULT 0,
          max_each    INTEGER NOT NULL DEFAULT 10,
          active      INTEGER NOT NULL DEFAULT 1,
          sort_order  INTEGER NOT NULL DEFAULT 0,
          created_at  INTEGER NOT NULL
        )""")
        conn.execute(
            "INSERT INTO products (id,name,description,price,quantity,sold,"
            "max_each,active,sort_order,created_at) "
            "SELECT id,name,description,price,quantity,sold,max_each,active,"
            "sort_order,created_at FROM products_old")
        conn.execute("DROP TABLE products_old")

    ocols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    if "buyer_phone" not in ocols:
        # Phone number, so you can chase a no-show or an abandoned cart.
        conn.execute("ALTER TABLE orders ADD COLUMN buyer_phone TEXT NOT NULL DEFAULT ''")
    if "discount_id" not in ocols:
        # What was applied, and how much came off. `total` stays the amount the
        # customer ACTUALLY paid, so revenue figures never need adjusting.
        conn.execute("ALTER TABLE orders ADD COLUMN discount_id TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN discount_code TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE orders ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE orders ADD COLUMN subtotal INTEGER NOT NULL DEFAULT 0")
    if "booking_fee" not in ocols:
        # Booking fee, charged per order to cover Stripe's cut. Shown separately
        # at checkout; `total` includes it.
        conn.execute("ALTER TABLE orders ADD COLUMN booking_fee INTEGER NOT NULL DEFAULT 0")
    if "terms_accepted_at" not in ocols:
        # Proof of acceptance. A tickbox is worthless if you can't later show WHAT
        # they agreed to — terms change, so we stamp the version they saw.
        conn.execute("ALTER TABLE orders ADD COLUMN terms_accepted_at INTEGER")
        conn.execute("ALTER TABLE orders ADD COLUMN terms_version INTEGER NOT NULL DEFAULT 0")
    if "refunded_at" not in ocols:
        # Refunds: void the tickets so a refunded customer can't still walk in.
        conn.execute("ALTER TABLE orders ADD COLUMN refunded_at INTEGER")
        conn.execute("ALTER TABLE orders ADD COLUMN payment_intent TEXT")

    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "image" not in ecols:
        # A real cover image (uploaded file path or external URL). The original
        # `image_url` column was misnamed — it only ever held a hex accent colour.
        conn.execute("ALTER TABLE events ADD COLUMN image TEXT NOT NULL DEFAULT ''")
    if "address" not in ecols:
        # Full venue address, so punters can get directions. `venue` is just the
        # name ("The Social Club"); this is the postal address.
        conn.execute("ALTER TABLE events ADD COLUMN address TEXT NOT NULL DEFAULT ''")
    if "archived_at" not in ecols:
        # Archiving hides an event from the dashboard and the public list. It is
        # NOT deletion: tickets stay scannable, orders and revenue stay intact.
        # `published` is a different thing (draft vs live) — a past event is
        # normally published AND archived.
        conn.execute("ALTER TABLE events ADD COLUMN archived_at INTEGER")


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


def list_events(only_published=False, include_past=True, archived=False):
    """archived=False -> live events (the default everywhere).
       archived=True  -> only the archive.
       archived=None  -> everything, archived or not."""
    q = "SELECT * FROM events"
    conds = []
    if only_published:
        conds.append("published = 1")
    if not include_past:
        conds.append(f"starts_at >= {now()}")
    if archived is True:
        conds.append("archived_at IS NOT NULL")
    elif archived is False:
        conds.append("archived_at IS NULL")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY starts_at ASC" if not archived else " ORDER BY starts_at DESC"
    with cursor() as conn:
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def archive_event(eid, archived=True):
    """Hide an event from the dashboard and the public list.

    Deliberately NOT a delete. Tickets stay valid and scannable (someone might
    turn up late, or you might need to check a name weeks later), orders and
    revenue stay intact, and it can be undone.
    """
    with cursor() as conn:
        conn.execute("UPDATE events SET archived_at = ? WHERE id = ?",
                     (now() if archived else None, eid))


def archivable_events():
    """Past events that aren't archived yet — offered as a one-click tidy-up."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE archived_at IS NULL AND starts_at < ? "
            "ORDER BY starts_at DESC", (now(),)).fetchall()
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
class PriceChanged(Exception):
    """A price tier moved on between the page loading and checkout.

    We refuse the order rather than charging a price the customer never agreed to.
    The handler shows them the new price and lets them confirm.
    """
    def __init__(self, changes):
        self.changes = changes
        super().__init__("Price changed")


def create_order(event_id, buyer_name, buyer_email, items, provider="mock",
                 currency="GBP", buyer_phone="", discount_code="",
                 quoted_prices=None, accept_terms=False, products=None):
    """items: list of (ticket_type_id, qty). Validates stock. Returns order id.

    Raises ValueError if a ticket type is sold out / lacks stock, or if a supplied
    discount code is invalid.
    """
    oid = new_id("ord")
    price_changes = []          # tiers that moved on while they were deciding
    with cursor() as conn:
        subtotal = 0
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

            # Price NOW, not the price on the page they loaded ten minutes ago.
            # A tier may have sold out or expired in between; we charge the real
            # current price and tell them (see quoted_price below).
            unit, tier_name = effective_price(dict(tt))
            if quoted_prices and str(tt_id) in quoted_prices:
                try:
                    was = int(quoted_prices[str(tt_id)])
                except (TypeError, ValueError):
                    was = unit
                if was != unit:
                    price_changes.append({
                        "name": tt["name"], "was": was, "now": unit,
                        "tier": tier_name,
                    })

            subtotal += unit * qty
            resolved.append((tt_id, qty, unit))
        if not resolved:
            raise ValueError("No tickets selected")

        # Add-ons (dabbers, vouchers). Stock is GLOBAL — a box of 40 dabbers is one
        # box, shared across every event, so this can't oversell across nights.
        resolved_products = []
        for pid, qty in (products or []):
            if qty <= 0:
                continue
            pr = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if pr is None:
                raise ValueError("Unknown product")
            if not pr["active"]:
                raise ValueError(f"{pr['name']} is no longer available.")
            if qty > pr["max_each"]:
                raise ValueError(f"Maximum {pr['max_each']} {pr['name']} per booking.")
            if pr["quantity"] is not None:          # NULL = unlimited
                left = pr["quantity"] - pr["sold"]
                if qty > left:
                    raise ValueError(
                        f"Only {left} {pr['name']} left." if left > 0
                        else f"{pr['name']} has sold out.")
            subtotal += pr["price"] * qty
            resolved_products.append((pid, qty, pr["price"]))

    # If the price moved, don't silently charge more — stop and tell them.
    if price_changes:
        raise PriceChanged(price_changes)

    # Validate the code against the real subtotal. Outside the transaction above
    # because validate_discount opens its own cursor.
    disc, off = (None, 0)
    if discount_code:
        disc, off = validate_discount(discount_code, event_id, subtotal)

    after_discount = subtotal - off
    # Fee is per ORDER (not per ticket) and calculated after the discount, so a
    # code doesn't inflate the fee. The customer pays tickets - discount + fee.
    fee = calc_booking_fee(after_discount)
    total = after_discount + fee

    # Terms: refuse the order if terms exist and weren't accepted. Enforced on the
    # SERVER — a required checkbox in HTML is trivially bypassed.
    terms = get_terms()
    if terms["text"] and not accept_terms:
        raise ValueError("Please accept the terms and conditions.")
    accepted_at = now() if terms["text"] else None

    with cursor() as conn:
        conn.execute(
            "INSERT INTO orders (id,event_id,buyer_name,buyer_email,buyer_phone,"
            "total,subtotal,discount_id,discount_code,discount_amount,booking_fee,"
            "terms_accepted_at,terms_version,"
            "currency,status,provider,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, event_id, buyer_name, buyer_email, buyer_phone or "", total,
             subtotal, disc["id"] if disc else None,
             disc["code"] if disc else "", off, fee,
             accepted_at, terms["version"],
             currency, "pending", provider, now()),
        )
        for pid, qty, price in resolved_products:
            conn.execute(
                "INSERT INTO order_products (id,order_id,product_id,qty,unit_price)"
                " VALUES (?,?,?,?,?)",
                (new_id("op"), oid, pid, qty, price),
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

    # Count the discount only now the money has actually arrived. Counting it at
    # checkout would let abandoned carts burn through a limited code's uses.
    did = order["discount_id"] if "discount_id" in order.keys() else None
    if did:
        redeem_discount(did, oid, order["discount_amount"])

    with cursor() as conn:
        conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (oid,))

        # Add-on stock comes off at PAYMENT, same as tickets — so an abandoned
        # cart doesn't sit on the last dabber.
        for op in conn.execute("SELECT * FROM order_products WHERE order_id = ?",
                               (oid,)).fetchall():
            conn.execute("UPDATE products SET sold = sold + ? WHERE id = ?",
                         (op["qty"], op["product_id"]))

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
        # What they've bought that you have to physically hand over. Without this
        # you've taken the money and have no idea who's owed a dabber.
        p["products"] = products_for_order(p["order_id"])
        out.append(p)
    # Still-to-come first — that's what you're looking for on the night.
    order = {"waiting": 0, "partial": 1, "in": 2}
    out.sort(key=lambda p: (order[p["state"]], (p["buyer_name"] or "").lower()))
    return out


def known_venues():
    """Every venue+address you've used before, most-recent first.

    There's no separate venues table — events already hold this. Deduping on the
    venue name means picking one gives you the address you last used for it, so
    you never retype (or mistype) a repeat venue.
    """
    with cursor() as conn:
        rows = conn.execute(
            "SELECT venue, address, MAX(starts_at) AS last_used "
            "FROM events "
            "WHERE TRIM(venue) != '' "
            "GROUP BY LOWER(TRIM(venue)) "
            "ORDER BY last_used DESC"
        ).fetchall()
    return [{"venue": r["venue"], "address": r["address"] or ""} for r in rows]


def all_orders(status=None, search="", limit=500):
    """Every order across every event — so you don't have to dig into each one.

    status: 'paid' | 'pending' (an abandoned cart) | None for all.

    An abandoned cart isn't a separate thing: it's an order that was created at
    checkout but never paid for. We already record those, we just never showed them.
    """
    sql = (
        "SELECT o.id, o.buyer_name, o.buyer_email, o.buyer_phone, o.total, "
        "o.currency, o.status, o.created_at, o.provider, "
        "o.terms_accepted_at, o.terms_version, "
        "e.id AS event_id, e.title AS event_title, e.starts_at, "
        "(SELECT COUNT(*) FROM tickets t WHERE t.order_id = o.id) AS ticket_count, "
        "(SELECT SUM(oi.qty) FROM order_items oi WHERE oi.order_id = o.id) AS item_qty "
        "FROM orders o JOIN events e ON e.id = o.event_id"
    )
    params, where = [], []
    if status == "paid":
        # "Paid" shouldn't include refunded ones — they're a separate state.
        where.append("o.status = 'paid'")
    elif status:
        where.append("o.status = ?")
        params.append(status)
    if search:
        where.append("(LOWER(o.buyer_name) LIKE ? OR LOWER(o.buyer_email) LIKE ? "
                     "OR o.buyer_phone LIKE ? OR LOWER(e.title) LIKE ?)")
        q = f"%{search.lower()}%"
        params += [q, q, q, q]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY o.created_at DESC LIMIT ?"
    params.append(limit)

    with cursor() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def orders_summary():
    """Headline numbers for the all-orders page."""
    with cursor() as conn:
        paid = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) v FROM orders WHERE status='paid'"
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) v FROM orders WHERE status='pending'"
        ).fetchone()
    return {
        "paid_count": paid["c"], "revenue": paid["v"],
        "abandoned_count": pending["c"], "abandoned_value": pending["v"],
    }


# ---------------------------------------------------------------------------
# Discount codes
# ---------------------------------------------------------------------------
def create_discount(code, kind, value, event_id=None, max_uses=None,
                    expires_at=None):
    """kind: 'percent' (value 1-100) or 'fixed' (value in pence)."""
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("Enter a code.")
    if kind not in ("percent", "fixed"):
        raise ValueError("Unknown discount type.")
    value = int(value)
    if kind == "percent" and not (1 <= value <= 100):
        raise ValueError("A percentage must be between 1 and 100.")
    if kind == "fixed" and value < 1:
        raise ValueError("The amount off must be more than zero.")
    if max_uses is not None and int(max_uses) < 1:
        raise ValueError("Usage limit must be at least 1 (or leave it blank).")

    did = new_id("dsc")
    with cursor() as conn:
        exists = conn.execute("SELECT 1 FROM discounts WHERE code = ?", (code,)).fetchone()
        if exists:
            raise ValueError(f"The code {code} already exists.")
        conn.execute(
            "INSERT INTO discounts (id,code,kind,value,event_id,max_uses,"
            "used_count,expires_at,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (did, code, kind, value, event_id or None,
             int(max_uses) if max_uses else None, 0,
             int(expires_at) if expires_at else None, 1, now()))
    return did


def list_discounts():
    with cursor() as conn:
        rows = conn.execute(
            "SELECT d.*, e.title AS event_title FROM discounts d "
            "LEFT JOIN events e ON e.id = d.event_id "
            "ORDER BY d.created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_discount_by_code(code):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM discounts WHERE code = ?",
                           ((code or "").strip().upper(),)).fetchone()
    return dict(row) if row else None


def set_discount_active(did, active):
    with cursor() as conn:
        conn.execute("UPDATE discounts SET active = ? WHERE id = ?",
                     (1 if active else 0, did))


def delete_discount(did):
    with cursor() as conn:
        conn.execute("DELETE FROM discounts WHERE id = ?", (did,))


def validate_discount(code, event_id, subtotal):
    """Check a code and work out what it's worth.

    Returns (discount_dict, amount_off_pence) or raises ValueError with a message
    the customer sees. Every rejection path is explicit — a discount that silently
    fails, or silently applies when it shouldn't, is a money bug.
    """
    d = get_discount_by_code(code)
    if not d:
        raise ValueError("That code isn't recognised.")
    if not d["active"]:
        raise ValueError("That code is no longer active.")
    if d["expires_at"] and now() > d["expires_at"]:
        raise ValueError("That code has expired.")
    if d["event_id"] and d["event_id"] != event_id:
        raise ValueError("That code isn't valid for this event.")
    if d["max_uses"] is not None and d["used_count"] >= d["max_uses"]:
        raise ValueError("That code has been fully used.")

    if d["kind"] == "percent":
        off = (subtotal * d["value"]) // 100
    else:
        off = d["value"]

    # Never discount below zero, and never let a fixed discount exceed the total.
    off = max(0, min(off, subtotal))
    if off <= 0:
        raise ValueError("That code doesn't reduce this order.")
    return d, off


def redeem_discount(did, order_id, amount_off):
    """Record a redemption. Called only when an order is actually PAID.

    Counting at payment (not at checkout) is deliberate: otherwise abandoned carts
    would burn through a limited code's uses without anyone paying.

    Returns False if the code ran out in the meantime — two people can be at the
    checkout with the last use of a code at the same time.
    """
    with cursor() as conn:
        # Re-check the limit inside the transaction, then increment atomically.
        row = conn.execute(
            "SELECT max_uses, used_count FROM discounts WHERE id = ?", (did,)).fetchone()
        if row is None:
            return False
        if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
            return False
        already = conn.execute(
            "SELECT 1 FROM discount_uses WHERE order_id = ?", (order_id,)).fetchone()
        if already:
            return True          # idempotent: don't double-count a re-confirmed order
        conn.execute("UPDATE discounts SET used_count = used_count + 1 WHERE id = ?",
                     (did,))
        conn.execute(
            "INSERT INTO discount_uses (id,discount_id,order_id,amount_off,created_at)"
            " VALUES (?,?,?,?,?)",
            (new_id("du"), did, order_id, amount_off, now()))
    return True


# ---------------------------------------------------------------------------
# Settings & booking fee
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "fee_percent": "0",     # e.g. "5" for 5%
    "fee_fixed": "0",       # pence, e.g. "20" for 20p
    "fee_label": "Booking fee",
    "terms_text": "",
    "terms_version": "0",
}


def get_terms():
    """The current terms, and which version they are.

    Version bumps on every edit. Orders record the version they accepted, so if
    you change the terms later you can still show exactly what a given customer
    agreed to — which is the whole point of the tickbox.
    """
    return {
        "text": get_setting("terms_text") or "",
        "version": int(get_setting("terms_version") or 0),
    }


def save_terms(text):
    text = (text or "").strip()
    cur = get_terms()
    if text == cur["text"]:
        return cur["version"]          # nothing changed, don't bump
    v = cur["version"] + 1
    set_setting("terms_text", text)
    set_setting("terms_version", v)
    # Keep the old wording, so a past order's version can still be displayed.
    if cur["text"]:
        set_setting(f"terms_text_v{cur['version']}", cur["text"])
    return v


def terms_version_text(version):
    """The wording of a specific past version (for proving what someone accepted)."""
    cur = get_terms()
    if version == cur["version"]:
        return cur["text"]
    return get_setting(f"terms_text_v{version}", "") or ""


def terms_required():
    """Only force acceptance if you've actually written some terms."""
    return bool(get_terms()["text"])


def get_setting(key, default=None):
    with cursor() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    with cursor() as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))


def fee_config():
    """The booking fee settings, as numbers."""
    try:
        pct = float(get_setting("fee_percent") or 0)
    except ValueError:
        pct = 0.0
    try:
        fixed = int(get_setting("fee_fixed") or 0)
    except ValueError:
        fixed = 0
    return {
        "percent": max(0.0, pct),
        "fixed": max(0, fixed),
        "label": get_setting("fee_label") or "Booking fee",
        "enabled": pct > 0 or fixed > 0,
    }


def calc_booking_fee(amount):
    """Booking fee for an order, charged ONCE per booking (not per ticket).

    Worked out on the amount actually being paid — i.e. AFTER any discount — so a
    discount code doesn't quietly inflate the fee.
    """
    cfg = fee_config()
    if not cfg["enabled"] or amount <= 0:
        return 0
    fee = int(round(amount * cfg["percent"] / 100.0)) + cfg["fixed"]
    return max(0, fee)


# ---------------------------------------------------------------------------
# Price tiers (early bird / quantity-based pricing)
# ---------------------------------------------------------------------------
def add_price_tier(ticket_type_id, name, price, until_date=None, max_qty=None):
    if not name.strip():
        raise ValueError("Give the tier a name.")
    if price < 0:
        raise ValueError("Price can't be negative.")
    if until_date is None and max_qty is None:
        raise ValueError("A tier needs a date limit, a quantity limit, or both.")
    if max_qty is not None and max_qty < 1:
        raise ValueError("Quantity limit must be at least 1.")
    tid = new_id("tier")
    with cursor() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM price_tiers WHERE ticket_type_id = ?",
                         (ticket_type_id,)).fetchone()["c"]
        conn.execute(
            "INSERT INTO price_tiers (id,ticket_type_id,name,price,until_date,"
            "max_qty,sort_order,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, ticket_type_id, name.strip(), price, until_date, max_qty, n, now()))
    return tid


def list_price_tiers(ticket_type_id):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM price_tiers WHERE ticket_type_id = ? "
            "ORDER BY sort_order, created_at", (ticket_type_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_price_tier(tid):
    with cursor() as conn:
        conn.execute("DELETE FROM price_tiers WHERE id = ?", (tid,))


def effective_price(tt):
    """THE price for a ticket type right now, and why.

    This is the single source of truth — the event page, the checkout and the
    Stripe charge all call it. If display and checkout used different logic they
    would drift, and someone would be charged a price they were never shown.

    A tier applies while BOTH its conditions hold (a tier with only one condition
    ignores the other). Tiers are checked in order; the first that still applies
    wins. If none do, the ticket type's base price is used.

    Returns (price_pence, tier_name_or_None).
    """
    tiers = list_price_tiers(tt["id"])
    if not tiers:
        return tt["price"], None

    sold = tt["sold"]
    t_now = now()
    for tier in tiers:
        # Date rule: still within the window?
        if tier["until_date"] is not None and t_now >= tier["until_date"]:
            continue
        # Quantity rule: still tickets left in this tier?
        if tier["max_qty"] is not None and sold >= tier["max_qty"]:
            continue
        return tier["price"], tier["name"]

    return tt["price"], None


def price_tier_summary(tt):
    """What's coming next, for an honest 'price rises to X' nudge on the page."""
    tiers = list_price_tiers(tt["id"])
    if not tiers:
        return None
    cur_price, cur_name = effective_price(tt)
    sold = tt["sold"]
    t_now = now()

    for tier in tiers:
        if tier["until_date"] is not None and t_now >= tier["until_date"]:
            continue
        if tier["max_qty"] is not None and sold >= tier["max_qty"]:
            continue
        # This is the active tier — say what makes it end.
        left = None
        if tier["max_qty"] is not None:
            left = max(0, tier["max_qty"] - sold)
        return {
            "name": tier["name"], "price": cur_price,
            "until_date": tier["until_date"], "left_in_tier": left,
        }
    return None


def void_order_tickets(oid, reason="refunded"):
    """Void every ticket on an order and put the stock back.

    A refund that leaves the ticket scannable is worse than useless — the customer
    gets their money back AND walks in. Returns how many tickets were voided.
    """
    with cursor() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
        if order is None:
            return 0
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE order_id = ? AND status != 'void'",
            (oid,)).fetchall()
        n = 0
        for t in tickets:
            conn.execute("UPDATE tickets SET status = 'void' WHERE id = ?", (t["id"],))
            # Return the ticket to stock so it can be resold.
            conn.execute(
                "UPDATE ticket_types SET sold = MAX(0, sold - 1) WHERE id = ?",
                (t["ticket_type_id"],))
            n += 1
        # Refunding also returns any add-ons to stock.
        for op in conn.execute("SELECT * FROM order_products WHERE order_id = ?",
                               (oid,)).fetchall():
            conn.execute("UPDATE products SET sold = MAX(0, sold - ?) WHERE id = ?",
                         (op["qty"], op["product_id"]))

        conn.execute(
            "UPDATE orders SET status = ?, refunded_at = ? WHERE id = ?",
            ("refunded", now(), oid))
    return n


def order_id_for_payment_intent(pi):
    with cursor() as conn:
        row = conn.execute(
            "SELECT id FROM orders WHERE payment_intent = ?", (pi,)).fetchone()
    return row["id"] if row else None


def set_payment_intent(oid, pi):
    with cursor() as conn:
        conn.execute("UPDATE orders SET payment_intent = ? WHERE id = ?", (pi, oid))


def update_order_email(oid, email):
    """Correct a mistyped address, so any future email reaches them."""
    with cursor() as conn:
        conn.execute("UPDATE orders SET buyer_email = ? WHERE id = ?",
                     ((email or "").strip(), oid))


def paid_orders_for_email(email):
    """Every paid, non-refunded order for an email address, newest first.

    Used by the public 'resend my tickets' page. Only PAID orders — an abandoned
    cart has no tickets to send.
    """
    email = (email or "").strip().lower()
    if not email:
        return []
    with cursor() as conn:
        rows = conn.execute(
            "SELECT o.*, e.title AS event_title, e.starts_at "
            "FROM orders o JOIN events e ON e.id = o.event_id "
            "WHERE LOWER(o.buyer_email) = ? AND o.status = 'paid' "
            "ORDER BY o.created_at DESC", (email,)).fetchall()
    return [dict(r) for r in rows]


def find_orders(query):
    """Admin lookup: find an order by name, email or phone.

    Deliberately broader than the public one — the whole point is to rescue someone
    who mistyped their email, so searching by their email alone wouldn't find them.
    """
    q = f"%{(query or '').strip().lower()}%"
    if not query.strip():
        return []
    with cursor() as conn:
        rows = conn.execute(
            "SELECT o.*, e.title AS event_title, e.starts_at, "
            "(SELECT COUNT(*) FROM tickets t WHERE t.order_id = o.id) AS ticket_count "
            "FROM orders o JOIN events e ON e.id = o.event_id "
            "WHERE o.status = 'paid' AND ("
            "  LOWER(o.buyer_name) LIKE ? OR LOWER(o.buyer_email) LIKE ? "
            "  OR REPLACE(o.buyer_phone,' ','') LIKE ?) "
            "ORDER BY o.created_at DESC LIMIT 50",
            (q, q, q.replace(" ", ""))).fetchall()
    return [dict(r) for r in rows]


def capacity_state(eid):
    """How full is this event? Used for the sold-out alerts."""
    with cursor() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) cap, COALESCE(SUM(sold),0) sold "
            "FROM ticket_types WHERE event_id = ?", (eid,)).fetchone()
    cap = row["cap"] or 0
    sold = row["sold"] or 0
    pct = int(round(sold * 100 / cap)) if cap else 0
    return {"capacity": cap, "sold": sold, "percent": pct,
            "remaining": max(0, cap - sold)}


def claim_capacity_alert(eid, threshold):
    """Claim the right to send ONE alert for this event at this threshold.

    Without this, every subsequent ticket sale past 80% would fire another email
    and you'd get spammed all evening. Returns True for exactly one caller.
    """
    key = f"capalert:{eid}:{threshold}"
    with cursor() as conn:
        try:
            conn.execute("INSERT INTO settings (key,value) VALUES (?,?)",
                         (key, str(now())))
            return True
        except sqlite3.IntegrityError:
            return False        # already sent


def reset_capacity_alerts(eid):
    """If you add more tickets, the alerts should be able to fire again."""
    with cursor() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE ?", (f"capalert:{eid}:%",))


def sales_over_time(eid=None, days=30):
    """Paid tickets and revenue per day — so you can see WHICH promo shifted tickets.

    Grouped by the day the order was PAID (created_at on a paid order), not the
    event date.
    """
    since = now() - days * 86400
    sql = (
        "SELECT o.created_at, o.total, o.discount_amount, o.booking_fee, "
        "(SELECT COUNT(*) FROM tickets t WHERE t.order_id = o.id) AS tickets "
        "FROM orders o WHERE o.status = 'paid' AND o.created_at >= ?"
    )
    params = [since]
    if eid:
        sql += " AND o.event_id = ?"
        params.append(eid)
    sql += " ORDER BY o.created_at"

    with cursor() as conn:
        rows = conn.execute(sql, params).fetchall()

    buckets = {}
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(int(r["created_at"])))
        b = buckets.setdefault(day, {"day": day, "tickets": 0, "revenue": 0, "orders": 0})
        b["tickets"] += r["tickets"] or 0
        b["revenue"] += r["total"] or 0
        b["orders"] += 1

    # Fill the gaps, so a quiet day shows as zero rather than vanishing.
    out = []
    for i in range(days, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(now() - i * 86400))
        out.append(buckets.get(day, {"day": day, "tickets": 0, "revenue": 0, "orders": 0}))
    return out


def sales_summary(eid=None):
    sql = ("SELECT COUNT(*) orders, COALESCE(SUM(total),0) revenue, "
           "COALESCE(SUM(discount_amount),0) discounts, "
           "COALESCE(SUM(booking_fee),0) fees FROM orders WHERE status = 'paid'")
    params = []
    if eid:
        sql += " AND event_id = ?"
        params.append(eid)
    with cursor() as conn:
        row = conn.execute(sql, params).fetchone()
        tix = conn.execute(
            "SELECT COUNT(*) c FROM tickets t JOIN orders o ON o.id = t.order_id "
            "WHERE o.status = 'paid'" + (" AND o.event_id = ?" if eid else ""),
            params).fetchone()["c"]
    return {"orders": row["orders"], "revenue": row["revenue"],
            "discounts": row["discounts"], "fees": row["fees"], "tickets": tix}


def busiest_day(eid=None, days=90):
    rows = sales_over_time(eid, days)
    best = max(rows, key=lambda r: r["tickets"], default=None)
    return best if best and best["tickets"] else None


def delete_event(eid, force=False):
    """PERMANENTLY delete an event and everything attached to it.

    Unlike archiving, this is irreversible: orders, tickets, price tiers and the
    event itself are gone. Intended for test events you want rid of.

    By default it REFUSES to delete an event with paid orders — that's your sales
    record, and a mis-click shouldn't destroy it. force=True overrides, but the
    caller must have explicitly confirmed.

    Returns a dict describing what was deleted.
    """
    with cursor() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
        if ev is None:
            raise ValueError("Unknown event.")

        paid = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) v FROM orders "
            "WHERE event_id = ? AND status IN ('paid','refunded')", (eid,)).fetchone()

        if paid["c"] and not force:
            raise ValueError(
                f"This event has {paid['c']} real order(s) worth "
                f"{paid['v']/100:.2f}. Deleting it would destroy that sales record.")

        tt_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM ticket_types WHERE event_id = ?", (eid,))]
        order_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM orders WHERE event_id = ?", (eid,))]

        n_tickets = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE event_id = ?", (eid,)).fetchone()["c"]

        # Children first, then the event.
        conn.execute("DELETE FROM tickets WHERE event_id = ?", (eid,))
        for oid in order_ids:
            conn.execute("DELETE FROM order_items WHERE order_id = ?", (oid,))
            conn.execute("DELETE FROM discount_uses WHERE order_id = ?", (oid,))
        conn.execute("DELETE FROM orders WHERE event_id = ?", (eid,))
        for ttid in tt_ids:
            conn.execute("DELETE FROM price_tiers WHERE ticket_type_id = ?", (ttid,))
        conn.execute("DELETE FROM ticket_types WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM discounts WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM settings WHERE key LIKE ?", (f"capalert:{eid}:%",))
        conn.execute("DELETE FROM events WHERE id = ?", (eid,))

    return {"title": ev["title"], "orders": len(order_ids),
            "tickets": n_tickets, "paid_orders": paid["c"],
            "revenue": paid["v"]}


def event_delete_summary(eid):
    """What WOULD be destroyed — shown before you confirm."""
    with cursor() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
        if ev is None:
            return None
        paid = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) v FROM orders "
            "WHERE event_id = ? AND status IN ('paid','refunded')", (eid,)).fetchone()
        orders = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE event_id = ?", (eid,)).fetchone()["c"]
        tickets = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE event_id = ?", (eid,)).fetchone()["c"]
    return {"id": eid, "title": ev["title"], "starts_at": ev["starts_at"],
            "orders": orders, "tickets": tickets,
            "paid_orders": paid["c"], "revenue": paid["v"],
            "has_real_sales": bool(paid["c"])}


# ---------------------------------------------------------------------------
# Products (add-ons: dabbers, drinks vouchers, raffle strips)
# ---------------------------------------------------------------------------
def add_product(name, price, quantity=None, description="", max_each=10):
    """quantity=None means UNLIMITED (drinks vouchers you can always print more of)."""
    if not name.strip():
        raise ValueError("Give the product a name.")
    if price < 0:
        raise ValueError("Price can't be negative.")
    if quantity is not None and quantity < 1:
        raise ValueError("Stock must be at least 1, or leave it blank for unlimited.")
    pid = new_id("prd")
    with cursor() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        conn.execute(
            "INSERT INTO products (id,name,description,price,quantity,"
            "sold,max_each,active,sort_order,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, name.strip(), description.strip(), price, quantity,
             0, max(1, max_each), 1, n, now()))
    return pid


def update_product(pid, name=None, price=None, quantity=..., description=None,
                   max_each=None):
    """Edit a product. quantity=... means 'leave alone'; None means unlimited."""
    sets, params = [], []
    if name is not None:
        if not name.strip():
            raise ValueError("Give the product a name.")
        sets.append("name = ?"); params.append(name.strip())
    if price is not None:
        if price < 0:
            raise ValueError("Price can't be negative.")
        sets.append("price = ?"); params.append(price)
    if quantity is not ...:
        if quantity is not None:
            if quantity < 0:
                raise ValueError("Stock can't be negative.")
            # Don't let you set stock BELOW what you've already sold — that would
            # show a negative "left" figure and confuse everything downstream.
            cur = get_product(pid)
            if cur and quantity < cur["sold"]:
                raise ValueError(
                    f"You've already sold {cur['sold']}. Stock can't be lower than that.")
        sets.append("quantity = ?"); params.append(quantity)
    if description is not None:
        sets.append("description = ?"); params.append(description.strip())
    if max_each is not None:
        sets.append("max_each = ?"); params.append(max(1, max_each))
    if not sets:
        return
    params.append(pid)
    with cursor() as conn:
        conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = ?", params)


def list_products(only_active=False):
    """The whole catalogue — the same products are offered at every event."""
    q = "SELECT * FROM products"
    if only_active:
        q += " WHERE active = 1"
    q += " ORDER BY sort_order, created_at"
    with cursor() as conn:
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def available_products():
    """Active products that aren't sold out — what a buyer actually sees."""
    return [p for p in list_products(only_active=True)
            if p["quantity"] is None or p["sold"] < p["quantity"]]


def product_left(p):
    """How many left. None = unlimited."""
    if p["quantity"] is None:
        return None
    return max(0, p["quantity"] - p["sold"])


def get_product(pid):
    with cursor() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def delete_product(pid):
    with cursor() as conn:
        sold = conn.execute("SELECT sold FROM products WHERE id = ?",
                            (pid,)).fetchone()
        if sold and sold["sold"] > 0:
            # Don't orphan a paid order_products row — people have bought these.
            raise ValueError("People have already bought this. Turn it off instead.")
        conn.execute("DELETE FROM products WHERE id = ?", (pid,))


def set_product_active(pid, active):
    with cursor() as conn:
        conn.execute("UPDATE products SET active = ? WHERE id = ?",
                     (1 if active else 0, pid))


def restock_product(pid, add):
    """Add to the master stock — you've bought another box of dabbers."""
    with cursor() as conn:
        row = conn.execute("SELECT quantity FROM products WHERE id = ?",
                           (pid,)).fetchone()
        if row is None or row["quantity"] is None:
            return          # unlimited: nothing to restock
        conn.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?",
                     (max(0, int(add)), pid))


def product_sales_by_event(pid):
    """Where did these actually sell? Useful when stock is shared across nights."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT e.title, e.starts_at, SUM(op.qty) AS qty "
            "FROM order_products op "
            "JOIN orders o ON o.id = op.order_id "
            "JOIN events e ON e.id = o.event_id "
            "WHERE op.product_id = ? AND o.status = 'paid' "
            "GROUP BY e.id ORDER BY e.starts_at", (pid,)).fetchall()
    return [dict(r) for r in rows]


def products_for_order(oid):
    with cursor() as conn:
        rows = conn.execute(
            "SELECT op.*, p.name, p.description FROM order_products op "
            "JOIN products p ON p.id = op.product_id "
            "WHERE op.order_id = ? ORDER BY p.sort_order", (oid,)).fetchall()
    return [dict(r) for r in rows]


def set_product_collected(order_id, product_id, collected=True):
    """Tick an add-on off at the door — so you know it's been handed over."""
    with cursor() as conn:
        conn.execute(
            "UPDATE order_products SET collected = ? "
            "WHERE order_id = ? AND product_id = ?",
            (1 if collected else 0, order_id, product_id))


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
        if row["status"] == "void":
            # Refunded or cancelled. Without this check a refunded customer would
            # get their money back AND still be admitted.
            full = get_ticket_by_code(code)
            return "void", full
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
