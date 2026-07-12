"""Mayhem Bingo ticketing — HTTP server, Python standard library only.

Run:  python3 app/server.py   (or:  python3 run.py)
"""
import html as _html
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
import db
import mailer
import payments
import qrgen
import templates as T
from tokens import make_session, verify_session, new_id

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Uploaded event images. Kept next to the SQLite DB so they land on the SAME
# persistent disk on Render — the normal filesystem is wiped on every deploy, so
# anything stored in the app directory would vanish along with your event photos.
UPLOAD_DIR = os.environ.get(
    "TICKETFLOW_UPLOADS",
    os.path.join(os.path.dirname(db.DB_PATH), "uploads"),
)
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6MB — plenty for a poster, stops abuse


def save_upload(field_files, field="image_file"):
    """Save an uploaded image and return its public URL, or "" if none/invalid."""
    got = (field_files or {}).get(field)
    if not got:
        return ""
    fname, data = got
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        return ""
    if len(data) > MAX_IMAGE_BYTES:
        return ""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe = f"{new_id('img')}{ext}"
    with open(os.path.join(UPLOAD_DIR, safe), "wb") as f:
        f.write(data)
    return f"/uploads/{safe}"


def qr_svg(code):
    # Level Q gives strong error resilience; ticket codes stay version 1–2.
    return qrgen.make_svg(code, level="Q", module=8)


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        self.routes.append((method, re.compile(f"^{pattern}$"), handler))

    def match(self, method, path):
        for m, rx, h in self.routes:
            if m == method:
                mo = rx.match(path)
                if mo:
                    return h, mo.groupdict()
        return None, None


router = Router()


def route(method, pattern):
    def deco(fn):
        router.add(method, pattern, fn)
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "MayhemTickets"

    # ---- helpers -------------------------------------------------------
    def base_url(self):
        host = self.headers.get("Host", "localhost:8000")
        # Behind a TLS-terminating proxy (Render, Cloudflare, nginx) the real
        # scheme arrives in X-Forwarded-Proto. Stripe rejects non-HTTPS return
        # URLs in live mode, and the camera scanner needs a secure context, so
        # we must not hardcode http://.
        proto = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
        if not proto:
            proto = "https" if os.environ.get("FORCE_HTTPS") else "http"
        return f"{proto}://{host}"

    def cookies(self):
        raw = self.headers.get("Cookie", "")
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def is_admin(self):
        return verify_session(self.cookies().get("tf_session", ""), ADMIN_PASSWORD)

    def send_html(self, body, status=200, headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if headers:
            for k, v in headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        if headers:
            for k, v in headers:
                self.send_header(k, v)
        self.end_headers()

    def redirect_top(self, location):
        """Navigate the top-level window, escaping an iframe if we're in one.

        Payment pages (Stripe) set frame-blocking headers and will not render
        inside an iframe, so a plain 303 would leave the buyer staring at a
        blank frame. This sends the whole tab to the payment page instead.
        """
        safe = json.dumps(location)  # JS-safe string literal
        html = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Redirecting to payment…</title>"
            "<p style=\"font-family:system-ui;padding:24px\">Taking you to secure payment…</p>"
            "<script>var u=" + safe + ";try{(window.top||window).location.replace(u);}"
            "catch(e){window.location.replace(u);}</script>"
            "<noscript><a href=\"" + _html.escape(location, quote=True) + "\">Continue to payment</a></noscript>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        data = html.encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def form(self):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            flat, multi, self.files = self._parse_multipart(ctype)
            return flat, multi
        self.files = {}
        body = self.read_body().decode("utf-8")
        return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}, \
               parse_qs(body, keep_blank_values=True)

    def _parse_multipart(self, ctype):
        """Parse multipart/form-data (file uploads). Stdlib only.

        Returns (flat_fields, multi_fields, files) where files maps a field name
        to (filename, bytes).
        """
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m:
            return {}, {}, {}
        boundary = (m.group(1) or m.group(2)).strip().encode()
        body = self.read_body()

        flat, multi, files = {}, {}, {}
        for part in body.split(b"--" + boundary):
            if not part.strip() or part.strip() == b"--":
                continue
            head, _, data = part.partition(b"\r\n\r\n")
            if not _:
                continue
            data = data.rstrip(b"\r\n")
            headers = head.decode("utf-8", "replace")
            name_m = re.search(r'name="([^"]*)"', headers)
            if not name_m:
                continue
            name = name_m.group(1)
            file_m = re.search(r'filename="([^"]*)"', headers)
            if file_m:
                fname = file_m.group(1)
                if fname and data:
                    files[name] = (fname, data)
            else:
                val = data.decode("utf-8", "replace")
                flat[name] = val
                multi.setdefault(name, []).append(val)
        return flat, multi, files

    # ---- dispatch ------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/static/"):
            return self.serve_static(path)
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        handler, params = router.match("GET", path)
        if handler:
            try:
                return handler(self, **params)
            except Exception as e:  # pragma: no cover
                return self.send_html(T.layout("Error",
                    f'<div class="flash err">Server error: {T.esc(e)}</div>'), 500)
        return self.send_html(T.layout("Not found",
            '<h1>404</h1><p class="muted">That page doesn\'t exist. '
            '<a href="/">Go home →</a></p>'), 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        handler, params = router.match("POST", parsed.path)
        if handler:
            try:
                return handler(self, **params)
            except Exception as e:  # pragma: no cover
                return self.send_html(T.layout("Error",
                    f'<div class="flash err">Server error: {T.esc(e)}</div>'), 500)
        return self.send_html(T.layout("Not found", "<h1>404</h1>"), 404)

    def serve_static(self, path):
        name = os.path.basename(path)
        fp = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(fp):
            return self.send_html("not found", 404)
        ctype = ("text/css" if name.endswith(".css")
                 else "application/javascript" if name.endswith(".js")
                 else "image/png" if name.endswith(".png")
                 else "image/svg+xml" if name.endswith(".svg")
                 else "image/x-icon" if name.endswith(".ico")
                 else "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_upload(self, path):
        # basename() strips any ../ so a crafted URL can't escape the folder.
        name = os.path.basename(path)
        fp = os.path.join(UPLOAD_DIR, name)
        if not os.path.isfile(fp):
            return self.send_html("not found", 404)
        ext = os.path.splitext(name)[1].lower()
        ctype = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif",
        }.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("· " + (fmt % args) + "\n")


# =====================================================================
# Public routes
# =====================================================================
@route("GET", "/")
def home(h):
    events = db.list_events(only_published=True)
    tts = {e["id"]: db.list_ticket_types(e["id"]) for e in events}
    h.send_html(T.home(events, tts, payments.is_live()))


@route("GET", "/embed")
def home_embed(h):
    """The events list with no site chrome, for iframing into another site
    (e.g. mayhembingo.co.uk). Same content, no header/footer/nav, transparent
    background, and it posts its height to the parent so the frame can resize."""
    events = db.list_events(only_published=True)
    tts = {e["id"]: db.list_ticket_types(e["id"]) for e in events}
    h.send_html(T.home(events, tts, payments.is_live(), embed=True))


@route("GET", r"/events/(?P<eid>[\w]+)")
def event_detail(h, eid):
    event = db.get_event(eid)
    if not event or not event["published"]:
        return h.send_html(T.layout("Not found", "<h1>Event not found</h1>"), 404)
    tts = db.list_ticket_types(eid)
    err = "Payment cancelled — your tickets weren't purchased." if h.query.get("cancelled") else None
    h.send_html(T.event_detail(event, tts, payments.is_live(), error=err))


@route("POST", "/checkout")
def checkout(h):
    flat, multi = h.form()
    eid = flat.get("event_id", "")
    event = db.get_event(eid)
    if not event:
        return h.send_html(T.layout("Error", '<div class="flash err">Unknown event</div>'), 400)
    tts = db.list_ticket_types(eid)
    items = []
    for t in tts:
        qty = int(flat.get(f"qty_{t['id']}", "0") or 0)
        if qty > 0:
            items.append((t["id"], qty))
    name = flat.get("buyer_name", "").strip()
    email = flat.get("buyer_email", "").strip()
    if not items or not name or not email:
        return h.send_html(T.event_detail(event, tts, payments.is_live(),
            error="Please pick at least one ticket and enter your details."), 400)
    try:
        oid = db.create_order(eid, name, email, items,
                              provider=("stripe" if payments.is_live() else "mock"),
                              currency=event["currency"])
    except ValueError as e:
        return h.send_html(T.event_detail(event, tts, payments.is_live(), error=str(e)), 400)

    order = db.get_order(oid)
    line_items = [{"name": db.get_ticket_type(tt)["name"], "qty": q,
                   "unit_price": db.get_ticket_type(tt)["price"]} for tt, q in items]
    url, provider, session_id = payments.create_checkout(order, line_items, event, h.base_url())
    db.set_order_session(oid, session_id, provider)
    if provider == "stripe":
        # Stripe's hosted checkout refuses to render inside an iframe, and this
        # site may be embedded (e.g. in mayhembingo.co.uk). Navigate the TOP-level
        # window to the payment page rather than the frame. Unframed, this behaves
        # like an ordinary redirect. The mock provider is our own page, so it can
        # redirect normally.
        h.redirect_top(url)
    else:
        h.redirect(url)


# ---- mock payment provider -----------------------------------------
@route("GET", "/mock/pay")
def mock_pay(h):
    order = db.get_order(h.query.get("order", ""))
    if not order:
        return h.send_html(T.layout("Error", "<h1>Unknown order</h1>"), 404)
    event = db.get_event(order["event_id"])
    h.send_html(T.mock_pay(order, event, h.base_url()))


@route("POST", "/mock/confirm")
def mock_confirm(h):
    flat, _ = h.form()
    oid = flat.get("order", "")
    order = db.get_order(oid)
    if not order:
        return h.send_html(T.layout("Error", "<h1>Unknown order</h1>"), 404)
    db.mark_order_paid(oid)
    h.redirect(f"/checkout/success?order={oid}")


@route("POST", "/mock/cancel")
def mock_cancel(h):
    flat, _ = h.form()
    h.redirect(f"/events/{flat.get('event','')}?cancelled=1")


# ---- success + tickets ---------------------------------------------
@route("GET", "/checkout/success")
def success(h):
    oid = h.query.get("order", "")
    order = db.get_order(oid)
    if not order:
        return h.send_html(T.layout("Error", "<h1>Unknown order</h1>"), 404)
    # In Stripe live mode, verify the session really paid before issuing.
    if order["status"] != "paid":
        if order["provider"] == "stripe":
            sid = h.query.get("session_id") or order["session_id"]
            if payments.session_is_paid(sid):
                db.mark_order_paid(oid)
        # mock orders are marked paid at /mock/confirm
    order = db.get_order(oid)
    if order["status"] != "paid":
        return h.send_html(T.layout("Pending",
            '<div class="narrow" style="margin:0 auto"><div class="flash info">'
            'Payment not confirmed yet. If you completed checkout, refresh in a moment.'
            '</div><a href="/">← Home</a></div>'))
    event = db.get_event(order["event_id"])
    tickets = db.tickets_for_order(oid)
    svgs = {t["code"]: qr_svg(t["code"]) for t in tickets}

    # Email the tickets — exactly once per order, and never fatally. A failed
    # email must not stop the buyer seeing the tickets they've just paid for.
    emailed = bool(order.get("emailed_at"))
    if not emailed and mailer.is_configured() and db.claim_email_send(oid):
        ok = mailer.send(
            order["buyer_email"],
            f"Your tickets — {event['title']}",
            mailer.ticket_email_html(event, tickets, h.base_url()),
            mailer.ticket_email_text(event, tickets, h.base_url()),
        )
        if ok:
            emailed = True
        else:
            db.mark_email_unsent(oid)  # let it retry on refresh

    h.send_html(T.success(order, event, tickets, svgs,
                          emailed=emailed, email_on=mailer.is_configured()))


@route("GET", r"/t/(?P<code>[\w\-]+)")
def ticket_view(h, code):
    t = db.get_ticket_by_code(code)
    if not t:
        return h.send_html(T.layout("Not found",
            '<h1>Ticket not found</h1><p class="muted">This code isn\'t valid.</p>'), 404)
    h.send_html(T.ticket_page(t, qr_svg(code)))


# ---- scanning -------------------------------------------------------
@route("GET", "/scan")
def scan_page(h):
    h.send_html(T.scanner())


@route("POST", "/api/scan")
def api_scan(h):
    try:
        payload = json.loads(h.read_body().decode() or "{}")
    except json.JSONDecodeError:
        return h.send_json({"status": "invalid"}, 400)
    code = (payload.get("code") or "").strip()
    # Accept either a bare code or a full ticket URL.
    m = re.search(r"(TKT-[\w]+)", code)
    if m:
        code = m.group(1)
    status, ticket = db.redeem_ticket(code)
    resp = {"status": status}
    if ticket:
        resp["ticket"] = {
            "event_title": ticket["event_title"],
            "ticket_name": ticket["ticket_name"],
            "buyer_name": ticket.get("buyer_name"),
            "scanned_at": ticket.get("scanned_at"),
        }
        # Group context: a party of 4 arriving together shouldn't need 4 separate
        # scans, so tell the door how many tickets are on this booking.
        oid = ticket.get("order_id")
        if oid:
            party = db.order_ticket_state(oid)
            if len(party) > 1:
                remaining = sum(1 for t in party if t["status"] != "used")
                resp["order"] = {
                    "id": oid,
                    "buyer_name": ticket.get("buyer_name"),
                    "total": len(party),
                    "admitted": len(party) - remaining,
                    "remaining": remaining,
                }
    h.send_json(resp)


@route("POST", "/admin/test-email")
def admin_test_email(h):
    """Send a test email and report exactly what happened. Email failures are
    otherwise silent by design (they must never break a sale), which makes them
    very hard to diagnose — this makes the failure visible."""
    if not require_admin(h):
        return
    flat, _ = h.form()
    to = (flat.get("to") or "").strip()
    if not to:
        return h.send_json({"ok": False, "error": "Enter an email address."})
    if not mailer.is_configured():
        return h.send_json({"ok": False, "error":
            "No mail provider configured. Set RESEND_API_KEY (or SMTP_HOST) in "
            "your Render environment, then redeploy."})
    ok, err = mailer.send_verbose(
        to, "Mayhem Bingo — test email",
        "<p>This is a test from your ticket site. If you're reading this, "
        "ticket emails are working.</p>",
        "Test from your ticket site. Ticket emails are working.")
    return h.send_json({
        "ok": ok,
        "error": err,
        "from": mailer.from_address(),
        "reply_to": mailer.reply_to(),
    })


@route("GET", r"/admin/events/(?P<eid>[\w]+)/door")
def admin_door(h, eid):
    """Door list: every booking party, who's arrived and who's still to come.
    This is the page you keep open on the night."""
    if not require_admin(h):
        return
    event = db.get_event(eid)
    if not event:
        return h.send_html(T.layout("Error", "<h1>Unknown event</h1>"), 404)
    parties = db.event_attendance(eid)
    h.send_html(T.admin_door(event, parties))


@route("POST", "/api/admit-order")
def api_admit_order(h):
    """Admit every remaining ticket on one booking — the whole party at once."""
    try:
        payload = json.loads(h.read_body().decode() or "{}")
    except json.JSONDecodeError:
        return h.send_json({"status": "invalid"}, 400)
    oid = (payload.get("order_id") or "").strip()
    if not oid or not db.get_order(oid):
        return h.send_json({"status": "invalid"}, 404)
    admitted, already = db.admit_order(oid)
    party = db.order_ticket_state(oid)
    h.send_json({
        "status": "ok",
        "admitted": admitted,
        "already": already,
        "total": len(party),
        "buyer_name": party[0]["buyer_name"] if party else "",
    })


# =====================================================================
# Organiser dashboard
# =====================================================================
def require_admin(h):
    if not h.is_admin():
        h.redirect("/admin/login")
        return False
    return True


@route("GET", "/admin/login")
def admin_login(h):
    if h.is_admin():
        return h.redirect("/admin")
    h.send_html(T.admin_login())


@route("POST", "/admin/login")
def admin_login_post(h):
    flat, _ = h.form()
    if flat.get("password", "") == ADMIN_PASSWORD:
        token = make_session(ADMIN_PASSWORD)
        h.redirect("/admin", headers=[
            ("Set-Cookie", f"tf_session={token}; Path=/; HttpOnly; SameSite=Lax")])
    else:
        h.send_html(T.admin_login(error="Incorrect password."), 401)


@route("GET", "/admin/logout")
def admin_logout(h):
    h.redirect("/admin/login", headers=[
        ("Set-Cookie", "tf_session=; Path=/; Max-Age=0")])


@route("GET", "/admin")
def admin_dashboard(h):
    if not require_admin(h):
        return
    events = db.list_events()
    stats = {e["id"]: db.event_stats(e["id"]) for e in events}
    h.send_html(T.admin_dashboard(events, stats, payments.is_live(),
                                  mail_on=mailer.is_configured(),
                                  mail_from=mailer.from_address(),
                                  mail_reply=mailer.reply_to()))


@route("GET", "/admin/events/new")
def admin_new_event(h):
    if not require_admin(h):
        return
    h.send_html(T.admin_new_event())


@route("POST", "/admin/events/new")
def admin_create_event(h):
    if not require_admin(h):
        return
    flat, multi = h.form()
    title = flat.get("title", "").strip()
    if not title:
        return h.send_html(T.admin_new_event(error="Title is required."), 400)
    # parse datetime-local (YYYY-MM-DDTHH:MM)
    import time as _t
    starts = flat.get("starts_at", "")
    try:
        tm = _t.strptime(starts, "%Y-%m-%dT%H:%M")
        starts_at = int(_t.mktime(tm))
    except ValueError:
        return h.send_html(T.admin_new_event(error="Please provide a valid date & time."), 400)
    # Cover image: an uploaded file wins; otherwise an external URL if given.
    image = save_upload(getattr(h, "files", {}), "image_file") or flat.get("image", "").strip()

    eid = db.create_event(
        title=title, description=flat.get("description", ""),
        venue=flat.get("venue", ""), starts_at=starts_at,
        image_url=flat.get("image_url", "#4f46e5"),
        currency=flat.get("currency", "GBP"), published=True)
    if image:
        db.update_event(eid, image=image)
    # ticket types (indexed fields tt_name_N / tt_price_N / tt_qty_N)
    idxs = sorted({int(k.split("_")[-1]) for k in flat if k.startswith("tt_name_")})
    for i in idxs:
        name = flat.get(f"tt_name_{i}", "").strip()
        price = flat.get(f"tt_price_{i}", "").strip()
        qty = flat.get(f"tt_qty_{i}", "").strip()
        if name and price and qty:
            db.add_ticket_type(eid, name, int(round(float(price) * 100)), int(qty), sort=i)
    h.redirect(f"/admin/events/{eid}")


@route("GET", r"/admin/events/(?P<eid>[\w]+)")
def admin_event(h, eid):
    if not require_admin(h):
        return
    event = db.get_event(eid)
    if not event:
        return h.send_html(T.layout("Not found", "<h1>Event not found</h1>"), 404)
    h.send_html(T.admin_event(event, db.list_ticket_types(eid),
                              db.event_stats(eid), db.list_orders(eid),
                              payments.is_live()))


@route("POST", "/admin/ticket-types/add")
def admin_add_tt(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    eid = flat.get("event_id", "")
    try:
        db.add_ticket_type(eid, flat.get("name", "").strip(),
                           int(round(float(flat.get("price", "0")) * 100)),
                           int(flat.get("quantity", "0")))
    except (ValueError, TypeError):
        pass
    h.redirect(f"/admin/events/{eid}")


@route("POST", "/admin/ticket-types/delete")
def admin_del_tt(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    db.delete_ticket_type(flat.get("id", ""))
    h.redirect(f"/admin/events/{flat.get('event_id','')}")


@route("POST", "/admin/events/edit")
def admin_edit_event(h):
    """Update an existing event's details — including its image and description.

    Without this you'd have to delete and recreate an event just to fix a typo,
    which would destroy any tickets already sold for it.
    """
    if not require_admin(h):
        return
    flat, _ = h.form()
    eid = flat.get("id", "")
    if not db.get_event(eid):
        return h.send_html(T.layout("Error", "<h1>Unknown event</h1>"), 404)

    fields = {}
    for key in ("title", "venue", "description"):
        if key in flat:
            fields[key] = flat.get(key, "").strip()
    if flat.get("image_url"):
        fields["image_url"] = flat["image_url"]

    starts = flat.get("starts_at", "")
    if starts:
        import time as _t
        try:
            fields["starts_at"] = int(_t.mktime(_t.strptime(starts, "%Y-%m-%dT%H:%M")))
        except ValueError:
            pass

    # New uploaded image wins; else a pasted URL; else leave the existing one alone.
    new_image = save_upload(getattr(h, "files", {}), "image_file")
    if not new_image and flat.get("image", "").strip():
        new_image = flat["image"].strip()
    if new_image:
        fields["image"] = new_image
    if flat.get("remove_image"):
        fields["image"] = ""

    if fields:
        db.update_event(eid, **fields)
    h.redirect(f"/admin/events/{eid}")


@route("POST", "/admin/events/toggle")
def admin_toggle(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    ev = db.get_event(flat.get("id", ""))
    if ev:
        db.update_event(ev["id"], published=0 if ev["published"] else 1)
    h.redirect(f"/admin/events/{flat.get('id','')}")


# =====================================================================
def main():
    db.init_db()
    # Seed sample data on first run (empty DB) — but NOT in production, where the
    # fake demo events (jazz brunch, beer festival...) would be publicly visible
    # on a real ticket site. Set NO_SEED=1 on the live deployment.
    if not db.list_events() and not os.environ.get("NO_SEED"):
        try:
            import seed
            seed.run()
        except Exception as e:
            sys.stderr.write(f"(seed skipped: {e})\n")

    # Say plainly whether tickets will be emailed. This used to fail silently —
    # no key meant no email and no warning, which is impossible to diagnose from
    # the outside.
    if mailer.is_configured():
        print(f"  Ticket emails: ON  (from {mailer.from_address()}, "
              f"replies to {mailer.reply_to()})")
    else:
        print("  Ticket emails: OFF — no RESEND_API_KEY (or SMTP_HOST) set.")
        print("                 Buyers will see and can print their tickets, "
              "but nothing will be emailed.")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    mode = "Stripe test mode" if payments.is_live() else "MOCK payment mode"
    print("\n  Mayhem Bingo tickets running")
    print(f"  → http://localhost:{port}   ({mode})")
    print(f"  → Organiser dashboard: http://localhost:{port}/admin  (password: {ADMIN_PASSWORD})")
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
