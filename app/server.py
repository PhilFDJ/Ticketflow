"""Mayhem Bingo ticketing — HTTP server, Python standard library only.

Run:  python3 app/server.py   (or:  python3 run.py)
"""
import html as _html
import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
import db
import mailer
import payments
import wallet
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
MAX_IMAGE_BYTES = 8 * 1024 * 1024   # 8MB upload cap — a phone photo or Canva export
MAX_IMAGE_EDGE = 1400               # px: posters are downscaled to this longest edge


def save_upload(field_files, field="image_file"):
    """Save an uploaded poster and return its public URL.

    Returns "" if no file was given. Raises ValueError with a readable message if
    the file is unusable — the caller shows it, rather than the upload failing
    silently and the event quietly saving with no picture.

    Posters are downscaled: a full-size Canva export can be 4000px wide and
    several MB, which every visitor would then download just to see a card.
    """
    got = (field_files or {}).get(field)
    if not got:
        return ""
    fname, data = got
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError(
            f"'{fname}' isn't an image we can use. "
            f"Use a JPG, PNG or WEBP.")
    if len(data) > MAX_IMAGE_BYTES:
        mb = len(data) / (1024 * 1024)
        raise ValueError(
            f"That image is {mb:.1f}MB — too big (max 8MB). "
            f"Export it smaller, or save as a JPG.")

    data, ext = _shrink_image(data, ext)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe = f"{new_id('img')}{ext}"
    with open(os.path.join(UPLOAD_DIR, safe), "wb") as f:
        f.write(data)
    return f"/uploads/{safe}"


def _shrink_image(data, ext):
    """Downscale a big poster so pages stay fast on a phone.

    Pillow isn't installed on the server (the app installs nothing on deploy), so
    if it's unavailable we keep the original bytes — the size cap above still
    protects us from anything absurd.
    """
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(data))
        if max(im.size) <= MAX_IMAGE_EDGE:
            return data, ext
        im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
        out = _io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")
        im.save(out, format="JPEG", quality=86, optimize=True)
        return out.getvalue(), ".jpg"
    except Exception:
        return data, ext


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
    h.send_html(T.home(events, tts, payments.is_live(),
                       pay_mode=payments.mode_label()))


@route("GET", "/embed")
def home_embed(h):
    """The events list with no site chrome, for iframing into another site
    (e.g. mayhembingo.co.uk). Same content, no header/footer/nav, transparent
    background, and it posts its height to the parent so the frame can resize."""
    events = db.list_events(only_published=True)
    tts = {e["id"]: db.list_ticket_types(e["id"]) for e in events}
    h.send_html(T.home(events, tts, payments.is_live(), embed=True,
                       pay_mode=payments.mode_label()))


def _priced(tts):
    """Attach the CURRENT effective price to each ticket type.

    Both the event page and the checkout read prices through here, so what's shown
    is what's charged.
    """
    out = []
    for t in tts:
        t = dict(t)
        price, tier = db.effective_price(t)
        t["_price"] = price
        t["_tier"] = tier
        t["_tier_info"] = db.price_tier_summary(t)
        out.append(t)
    return out


@route("GET", r"/events/(?P<eid>[\w]+)")
def event_detail(h, eid):
    event = db.get_event(eid)
    if not event or not event["published"]:
        return h.send_html(T.layout("Not found", "<h1>Event not found</h1>"), 404)
    tts = _priced(db.list_ticket_types(eid))
    err = "Payment cancelled — your tickets weren't purchased." if h.query.get("cancelled") else None
    h.send_html(T.event_detail(event, tts, payments.is_live(), error=err,
                               fee_cfg=db.fee_config(),
                               show_remaining=db.get_setting("show_remaining") == "1",
                               terms_required=db.terms_required()))


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
    phone = flat.get("buyer_phone", "").strip()
    dcode = flat.get("discount_code", "").strip()
    # What the page QUOTED them. If a tier has moved on since, we refuse rather
    # than silently charging more (see db.PriceChanged).
    quoted = {k[len("quoted_"):]: v for k, v in flat.items() if k.startswith("quoted_")}
    accepted = flat.get("accept_terms") in ("1", "on", "true", "yes")
    if not items or not name or not email or not phone:
        return h.send_html(T.event_detail(event, _priced(tts), payments.is_live(),
            error="Please pick at least one ticket and enter your name, email and phone.",
            fee_cfg=db.fee_config(),
            show_remaining=db.get_setting("show_remaining") == "1",
            terms_required=db.terms_required()), 400)
    try:
        oid = db.create_order(eid, name, email, items,
                              provider=("stripe" if payments.is_live() else "mock"),
                              currency=event["currency"], buyer_phone=phone,
                              discount_code=dcode, quoted_prices=quoted,
                              accept_terms=accepted)
    except db.PriceChanged as pc:
        # Show them the new price and let them decide. Never charge a price they
        # weren't shown.
        return h.send_html(T.event_detail(event, _priced(db.list_ticket_types(eid)),
                                          payments.is_live(),
                                          fee_cfg=db.fee_config(),
                                          show_remaining=db.get_setting("show_remaining") == "1",
                                          terms_required=db.terms_required(),
                                          price_notice=pc.changes), 409)
    except ValueError as e:
        return h.send_html(T.event_detail(event, _priced(db.list_ticket_types(eid)),
                                          payments.is_live(),
                                          error=str(e), fee_cfg=db.fee_config(),
                                          show_remaining=db.get_setting("show_remaining") == "1",
                                          terms_required=db.terms_required()), 400)

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


@route("GET", r"/t/(?P<code>[\w\-]+)/pass")
def ticket_pass(h, code):
    """Serve a signed Apple Wallet pass for one ticket."""
    t = db.get_ticket_by_code(code)
    if not t:
        return h.send_html(T.layout("Not found", "<h1>Ticket not found</h1>"), 404)
    if not wallet.is_configured():
        return h.send_html(T.layout("Unavailable",
            '<div class="narrow" style="margin:0 auto">'
            '<div class="flash err">Apple Wallet isn\'t set up on this site yet.</div>'
            f'<a href="/t/{esc_code(code)}">← Back to your ticket</a></div>'), 404)
    event = db.get_event(t["event_id"])
    try:
        data = wallet.build_pass(t, event, h.base_url())
    except Exception as e:
        print(f"[wallet] pass build failed for {code}: {e}")
        return h.send_html(T.layout("Error",
            '<div class="narrow" style="margin:0 auto">'
            '<div class="flash err">Couldn\'t build the Wallet pass. '
            'Your ticket still works — show the QR at the door.</div>'
            f'<a href="/t/{esc_code(code)}">← Back to your ticket</a></div>'), 500)

    h.send_response(200)
    h.send_header("Content-Type", "application/vnd.apple.pkpass")
    # MUST be inline, not attachment. With `attachment`, iOS Safari tries to save
    # the file — and since iOS has no file manager for .pkpass, it just says
    # "Safari cannot download this file". Served inline, iOS recognises the MIME
    # type and hands it straight to Wallet.
    h.send_header("Content-Disposition",
                  f'inline; filename="mayhem-bingo-{esc_code(code)}.pkpass"')
    h.send_header("Content-Length", str(len(data)))
    # Don't let a stale pass be cached — the ticket's state can change.
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(data)


def esc_code(c):
    return re.sub(r"[^\w\-]", "", c or "")


@route("GET", r"/t/(?P<code>[\w\-]+)")
def ticket_view(h, code):
    t = db.get_ticket_by_code(code)
    if not t:
        return h.send_html(T.layout("Not found",
            '<h1>Ticket not found</h1><p class="muted">This code isn\'t valid.</p>'), 404)
    h.send_html(T.ticket_page(t, qr_svg(code), wallet_on=wallet.is_configured()))


# ---- scanning -------------------------------------------------------
@route("GET", "/scan")
def scan_page(h):
    """Door scanner — ADMIN ONLY.

    This was public, which meant anyone who found the URL could admit tickets
    (including their own). Redirect to login rather than 404, so you can get
    straight in on the night.
    """
    if not require_admin(h, next_url="/scan"):
        return
    h.send_html(T.scanner())


@route("POST", "/api/scan")
def api_scan(h):
    # ADMIN ONLY. Hiding the scanner page is pointless if this endpoint is open —
    # anyone could POST a code and admit themselves.
    if not h.is_admin():
        return h.send_json({"status": "unauthorised"}, 401)
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


@route("GET", "/terms")
def terms_page(h):
    """Public terms & conditions."""
    t = db.get_terms()
    if not t["text"]:
        return h.send_html(T.layout("Terms",
            '<div class="narrow" style="margin:0 auto">'
            '<h1>Terms &amp; conditions</h1>'
            '<p class="muted">No terms have been published yet.</p></div>'), 404)
    h.send_html(T.terms_page(t["text"]))


@route("GET", "/admin/terms")
def admin_terms(h):
    if not require_admin(h):
        return
    h.send_html(T.admin_terms(db.get_terms(),
                              saved=(h.query.get("saved") == "1")))


@route("POST", "/admin/terms")
def admin_terms_save(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    db.save_terms(flat.get("terms_text", ""))
    h.redirect("/admin/terms?saved=1")


@route("POST", "/admin/tiers/add")
def admin_tier_add(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    ttid = flat.get("ticket_type_id", "")
    tt = db.get_ticket_type(ttid)
    if not tt:
        return h.send_html(T.layout("Error", "<h1>Unknown ticket type</h1>"), 404)

    until = flat.get("until_date", "").strip()
    maxq = flat.get("max_qty", "").strip()
    until_ts = None
    if until:
        try:
            t = time.strptime(until, "%Y-%m-%d")
            # End of that day — "early bird until 1 Aug" means all of 1 Aug.
            until_ts = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday,
                                        23, 59, 59, 0, 0, -1)))
        except ValueError:
            until_ts = None
    try:
        price = int(round(float(flat.get("price", "0")) * 100))
        db.add_price_tier(ttid, flat.get("name", ""), price,
                          until_date=until_ts,
                          max_qty=int(maxq) if maxq else None)
    except ValueError as e:
        return h.redirect(f"/admin/events/{tt['event_id']}?tier_err={urllib.parse.quote(str(e))}")
    h.redirect(f"/admin/events/{tt['event_id']}")


@route("POST", "/admin/tiers/delete")
def admin_tier_delete(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    eid = flat.get("event_id", "")
    db.delete_price_tier(flat.get("id", ""))
    h.redirect(f"/admin/events/{eid}")


@route("POST", "/admin/settings/display")
def admin_save_display(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    db.set_setting("show_remaining", "1" if flat.get("show_remaining") else "0")
    h.redirect("/admin/discounts?fee=saved")


@route("POST", "/admin/settings/fee")
def admin_save_fee(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    try:
        pct = float(flat.get("fee_percent", "0") or 0)
        fixed_pounds = float(flat.get("fee_fixed", "0") or 0)
    except ValueError:
        return h.redirect("/admin/discounts?fee=bad")
    if pct < 0 or fixed_pounds < 0:
        return h.redirect("/admin/discounts?fee=bad")

    db.set_setting("fee_percent", pct)
    db.set_setting("fee_fixed", int(round(fixed_pounds * 100)))   # £ -> pence
    db.set_setting("fee_label", (flat.get("fee_label") or "Booking fee").strip())
    h.redirect("/admin/discounts?fee=saved")


@route("GET", "/admin/discounts")
def admin_discounts(h):
    if not require_admin(h):
        return
    h.send_html(T.admin_discounts(db.list_discounts(), db.list_events(),
                                  fee_cfg=db.fee_config(),
                                  saved=(h.query.get("fee") == "saved"),
                                  show_remaining=db.get_setting("show_remaining") == "1"))


@route("POST", "/admin/discounts/new")
def admin_discounts_new(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    kind = flat.get("kind", "percent")
    raw_value = flat.get("value", "").strip()
    expires_raw = flat.get("expires_at", "").strip()
    max_uses = flat.get("max_uses", "").strip()

    try:
        if kind == "percent":
            value = int(float(raw_value))
        else:
            value = int(round(float(raw_value) * 100))   # £ -> pence
    except ValueError:
        return h.send_html(T.admin_discounts(db.list_discounts(), db.list_events(),
                                             error="Enter a number for the value.",
                                             fee_cfg=db.fee_config()), 400)

    expires_at = None
    if expires_raw:
        try:
            # An expiry date means end-of-day, not midnight-that-morning.
            t = time.strptime(expires_raw, "%Y-%m-%d")
            expires_at = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday,
                                          23, 59, 59, 0, 0, -1)))
        except ValueError:
            return h.send_html(T.admin_discounts(db.list_discounts(), db.list_events(),
                                                 error="Invalid expiry date.",
                                                 fee_cfg=db.fee_config()), 400)

    try:
        db.create_discount(
            flat.get("code", ""), kind, value,
            event_id=flat.get("event_id") or None,
            max_uses=int(max_uses) if max_uses else None,
            expires_at=expires_at,
        )
    except ValueError as e:
        return h.send_html(T.admin_discounts(db.list_discounts(), db.list_events(),
                                             error=str(e), fee_cfg=db.fee_config()), 400)
    h.redirect("/admin/discounts")


@route("POST", "/admin/discounts/toggle")
def admin_discounts_toggle(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    did = flat.get("id", "")
    on = flat.get("active") == "1"
    db.set_discount_active(did, on)
    h.redirect("/admin/discounts")


@route("POST", "/admin/discounts/delete")
def admin_discounts_delete(h):
    if not require_admin(h):
        return
    flat, _ = h.form()
    db.delete_discount(flat.get("id", ""))
    h.redirect("/admin/discounts")


@route("GET", "/admin/orders")
def admin_orders(h):
    """Every order across every event, in one place — including abandoned carts."""
    if not require_admin(h):
        return
    status = h.query.get("status", "")
    if status not in ("paid", "pending"):
        status = ""
    search = (h.query.get("q") or "").strip()
    orders = db.all_orders(status=status or None, search=search)
    h.send_html(T.admin_orders(orders, db.orders_summary(), status, search))


@route("GET", "/admin/orders.csv")
def admin_orders_csv(h):
    """All orders as CSV — customers, contact details, and abandoned carts."""
    if not require_admin(h):
        return
    status = h.query.get("status", "")
    if status not in ("paid", "pending"):
        status = ""

    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Event", "Name", "Email", "Phone", "Tickets",
                "Total", "Status", "T&Cs accepted"])
    for o in db.all_orders(status=status or None, search="", limit=5000):
        w.writerow([
            time.strftime("%d/%m/%Y %H:%M", time.localtime(int(o["created_at"]))),
            o["event_title"], o["buyer_name"], o["buyer_email"],
            o["buyer_phone"] or "",
            o["ticket_count"] or o["item_qty"] or 0,
            f"{o['total']/100:.2f}",
            "PAID" if o["status"] == "paid" else "ABANDONED",
            (time.strftime("%d/%m/%Y %H:%M",
                           time.localtime(int(o["terms_accepted_at"])))
             + f" (v{o['terms_version']})") if o.get("terms_accepted_at") else "",
        ])
    data = buf.getvalue().encode("utf-8-sig")
    h.send_response(200)
    h.send_header("Content-Type", "text/csv; charset=utf-8")
    h.send_header("Content-Disposition", 'attachment; filename="orders.csv"')
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


@route("GET", r"/admin/events/(?P<eid>[\w]+)/sheet")
def admin_door_sheet(h, eid):
    """A printable door list — the paper backup if the scanner or the wifi dies.

    Sorted by surname-ish (whatever they typed), with tick boxes and ticket codes,
    so someone can be found and checked off by hand under pressure.
    """
    if not require_admin(h):
        return
    event = db.get_event(eid)
    if not event:
        return h.send_html(T.layout("Error", "<h1>Unknown event</h1>"), 404)
    parties = db.event_attendance(eid)
    # On paper you look people up by NAME, not by arrival status.
    parties = sorted(parties, key=lambda p: (p["buyer_name"] or "").lower())
    h.send_html(T.door_sheet(event, parties))


@route("GET", r"/admin/events/(?P<eid>[\w]+)/report.csv")
def admin_event_csv(h, eid):
    """Every ticket for an event, as CSV — a paper/spreadsheet backup for the door
    in case the scanner or the network lets you down on the night."""
    if not require_admin(h):
        return
    event = db.get_event(eid)
    if not event:
        return h.send_html(T.layout("Error", "<h1>Unknown event</h1>"), 404)

    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Email", "Ticket type", "Ticket code",
                "Status", "Checked in at", "Order ref", "Party size"])

    for p in db.event_attendance(eid):
        for t in p["tickets"]:
            scanned = ""
            if t["scanned_at"]:
                scanned = time.strftime("%d/%m/%Y %H:%M",
                                        time.localtime(int(t["scanned_at"])))
            w.writerow([
                p["buyer_name"] or "",
                p["buyer_email"] or "",
                t["ticket_name"] or "",
                t["code"],
                "IN" if t["status"] == "used" else "not arrived",
                scanned,
                p["order_id"],
                p["total"],
            ])

    data = buf.getvalue().encode("utf-8-sig")   # BOM so Excel opens it cleanly
    safe = re.sub(r"[^\w\-]+", "-", event["title"]).strip("-").lower()
    h.send_response(200)
    h.send_header("Content-Type", "text/csv; charset=utf-8")
    h.send_header("Content-Disposition",
                  f'attachment; filename="{safe}-door-list.csv"')
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


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
    # ADMIN ONLY — this admits a whole group in one call.
    if not h.is_admin():
        return h.send_json({"status": "unauthorised"}, 401)
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
def require_admin(h, next_url=None):
    if not h.is_admin():
        # Send them back where they were going after login — on the night you want
        # to land straight on the scanner, not the dashboard.
        if next_url:
            h.redirect(f"/admin/login?next={urllib.parse.quote(next_url)}")
        else:
            h.redirect("/admin/login")
        return False
    return True


@route("GET", "/admin/login")
def admin_login(h):
    nxt = _safe_next(h.query.get("next", ""))
    if h.is_admin():
        return h.redirect(nxt or "/admin")
    h.send_html(T.admin_login(next_url=nxt))


@route("POST", "/admin/login")
def admin_login_post(h):
    flat, _ = h.form()
    nxt = _safe_next(flat.get("next", ""))
    if flat.get("password", "") == ADMIN_PASSWORD:
        token = make_session(ADMIN_PASSWORD)
        h.redirect(nxt or "/admin", headers=[
            ("Set-Cookie", f"tf_session={token}; Path=/; HttpOnly; SameSite=Lax")])
    else:
        h.send_html(T.admin_login(error="Incorrect password.", next_url=nxt), 401)


def _safe_next(url):
    """Only allow same-site relative paths — never an off-site redirect."""
    url = (url or "").strip()
    if url.startswith("/") and not url.startswith("//"):
        return url
    return ""


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
                                  mail_reply=mailer.reply_to(),
                                  wallet_on=wallet.is_configured(),
                                  wallet_problem=wallet.config_problem(),
                                  pay_mode=payments.mode_label()))


@route("GET", "/admin/events/new")
def admin_new_event(h):
    if not require_admin(h):
        return
    h.send_html(T.admin_new_event(venues=db.known_venues()))


@route("POST", "/admin/events/new")
def admin_create_event(h):
    if not require_admin(h):
        return
    flat, multi = h.form()
    title = flat.get("title", "").strip()
    if not title:
        return h.send_html(T.admin_new_event(error="Title is required.",
                                             venues=db.known_venues()), 400)
    # parse datetime-local (YYYY-MM-DDTHH:MM)
    import time as _t
    starts = flat.get("starts_at", "")
    try:
        tm = _t.strptime(starts, "%Y-%m-%dT%H:%M")
        starts_at = int(_t.mktime(tm))
    except ValueError:
        return h.send_html(T.admin_new_event(error="Please provide a valid date & time.",
                                             venues=db.known_venues()), 400)
    # Cover image: an uploaded file wins; otherwise an external URL if given.
    try:
        image = save_upload(getattr(h, "files", {}), "image_file") or flat.get("image", "").strip()
    except ValueError as e:
        return h.send_html(T.admin_new_event(error=str(e), venues=db.known_venues()), 400)

    eid = db.create_event(
        title=title, description=flat.get("description", ""),
        venue=flat.get("venue", ""), starts_at=starts_at,
        image_url=flat.get("image_url", "#4f46e5"),
        currency=flat.get("currency", "GBP"), published=True)
    if image:
        db.update_event(eid, image=image)
    if flat.get("address", "").strip():
        db.update_event(eid, address=flat["address"].strip())
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
    tts = _priced(db.list_ticket_types(eid))
    tiers = {t["id"]: db.list_price_tiers(t["id"]) for t in tts}
    h.send_html(T.admin_event(event, tts,
                              db.event_stats(eid), db.list_orders(eid),
                              payments.is_live(), venues=db.known_venues(),
                              tiers_by_tt=tiers))


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
    for key in ("title", "venue", "description", "address"):
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
    try:
        new_image = save_upload(getattr(h, "files", {}), "image_file")
    except ValueError as e:
        event = db.get_event(eid)
        tts = db.list_ticket_types(eid)
        stats = db.event_stats(eid)
        orders = db.list_orders(eid)
        return h.send_html(T.admin_event(event, tts, stats, orders,
                                         payments.is_live(), error=str(e),
                                         venues=db.known_venues()), 400)
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
    mode = {
        "live": "*** LIVE — REAL CARDS WILL BE CHARGED ***",
        "test": "Stripe TEST mode (no real money)",
        "mock": "MOCK payment mode (Stripe not connected)",
    }[payments.mode_label()]
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
