"""Payment handling: Stripe Checkout (test mode) with a built-in mock fallback.

No third-party SDK — talks to the Stripe REST API with urllib, so the only
requirement to go live with test payments is a Stripe test secret key in the
environment. With no key set, the app uses a self-contained mock checkout so it
runs end-to-end out of the box.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

STRIPE_API = "https://api.stripe.com/v1"


def stripe_key():
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def is_live():
    """True if ANY Stripe key is set (i.e. we're not in mock mode).

    NOTE: this does not mean real money — see is_real_money(). The name is
    historical; it distinguishes 'Stripe is wired up' from 'mock checkout'.
    """
    return bool(stripe_key())


def is_real_money():
    """True only for a LIVE Stripe key — i.e. real cards, real money.

    Stripe keys are prefixed: sk_test_... is a sandbox, sk_live_... is real. The
    dashboard used to say "test mode" for both, which is exactly the sort of thing
    that gets someone taking real payments while believing they're testing.
    """
    return stripe_key().startswith("sk_live_")


def mode_label():
    """How to describe the current payment mode, in plain words."""
    key = stripe_key()
    if not key:
        return "mock"          # no Stripe at all — simulated checkout
    if key.startswith("sk_live_"):
        return "live"          # REAL MONEY
    return "test"              # Stripe sandbox


def fee_label():
    """What the booking fee is called on the Stripe receipt and at checkout."""
    import db as _db
    return _db.fee_config()["label"]


def webhook_secret():
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()


def verify_webhook(payload: bytes, sig_header: str, tolerance=300):
    """Verify a Stripe webhook signature. Returns the parsed event, or raises.

    This is NOT optional. Without it, anyone who finds the webhook URL could POST
    a fake "payment succeeded" event and mint themselves free tickets. Stripe signs
    every webhook with a shared secret; we recompute the signature and compare.
    """
    import hashlib
    import hmac

    secret = webhook_secret()
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not set.")
    if not sig_header:
        raise ValueError("No Stripe-Signature header.")

    # Header looks like: t=1614556800,v1=abc123...,v1=def456...
    parts = {}
    for chunk in sig_header.split(","):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        parts.setdefault(k.strip(), []).append(v.strip())

    timestamps = parts.get("t", [])
    signatures = parts.get("v1", [])
    if not timestamps or not signatures:
        raise ValueError("Malformed Stripe-Signature header.")

    ts = timestamps[0]
    # Reject old events — stops someone replaying a captured webhook later.
    try:
        age = abs(int(time.time()) - int(ts))
    except ValueError:
        raise ValueError("Bad timestamp in signature.")
    if age > tolerance:
        raise ValueError(f"Webhook timestamp is {age}s old — rejected.")

    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

    # constant-time compare against each provided signature
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise ValueError("Webhook signature did not match.")

    return json.loads(payload.decode())


def _form_encode(data, parent=None):
    """Encode nested dict/list into Stripe's bracketed form format."""
    items = []
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{parent}[{k}]" if parent else k
            items += _form_encode(v, key)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            key = f"{parent}[{i}]"
            items += _form_encode(v, key)
    else:
        items.append((parent, str(data)))
    return items


def _stripe_post(path, data):
    body = urllib.parse.urlencode(_form_encode(data)).encode()
    req = urllib.request.Request(
        f"{STRIPE_API}{path}", data=body,
        headers={"Authorization": f"Bearer {stripe_key()}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _stripe_get(path):
    req = urllib.request.Request(
        f"{STRIPE_API}{path}",
        headers={"Authorization": f"Bearer {stripe_key()}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def create_checkout(order, items, event, base_url):
    """Return (checkout_url, provider, session_id).

    items: list of dicts with keys name, qty, unit_price (pence).
    """
    if not is_live():
        # Mock provider — the app renders its own fake card page.
        return f"{base_url}/mock/pay?order={order['id']}", "mock", None

    line_items = [{
        "price_data": {
            "currency": event["currency"].lower(),
            "product_data": {"name": f"{event['title']} — {it['name']}"},
            "unit_amount": it["unit_price"],
        },
        "quantity": it["qty"],
    } for it in items]

    # The booking fee must be CHARGED, not just displayed. Add it as its own line
    # so it appears on the customer's Stripe receipt exactly as it did at checkout.
    fee = order["booking_fee"] if "booking_fee" in order.keys() else 0
    if fee and fee > 0:
        line_items.append({
            "price_data": {
                "currency": event["currency"].lower(),
                "product_data": {"name": fee_label()},
                "unit_amount": fee,
            },
            "quantity": 1,
        })

    payload = {
        "mode": "payment",
        "success_url": f"{base_url}/checkout/success?order={order['id']}"
                       "&session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": f"{base_url}/events/{event['id']}?cancelled=1",
        "customer_email": order["buyer_email"],
        "client_reference_id": order["id"],
        "metadata": {"order_id": order["id"]},
        "line_items": line_items,
    }

    # CRITICAL: line_items are at full price, so without this Stripe would charge
    # the customer the FULL amount while our page showed them a discounted total.
    # Stripe applies discounts via a coupon on the session, so mint a one-off
    # coupon for exactly the amount we took off.
    discount_amount = order["discount_amount"] if "discount_amount" in order.keys() else 0
    if discount_amount and discount_amount > 0:
        coupon = _stripe_post("/coupons", {
            "amount_off": discount_amount,
            "currency": event["currency"].lower(),
            "duration": "once",
            "name": (order["discount_code"] or "Discount")[:40],
            # Stripe keeps coupons around; this one is for a single checkout.
            "max_redemptions": 1,
        })
        payload["discounts"] = [{"coupon": coupon["id"]}]

    session = _stripe_post("/checkout/sessions", payload)
    return session["url"], "stripe", session["id"]


def refund_order(order):
    """Refund a Stripe payment in full. Returns (ok, message).

    Only handles the Stripe side — voiding the tickets is the caller's job (see
    db.void_order_tickets), because a mock/cash order still needs voiding.
    """
    if not is_live():
        return True, "Mock order — nothing to refund at Stripe."
    pi = order.get("payment_intent")
    if not pi:
        # Fall back: look the session up to find its payment intent.
        sid = order.get("session_id")
        if not sid:
            return False, "No Stripe payment recorded for this order."
        try:
            session = _stripe_get(f"/checkout/sessions/{sid}")
            pi = session.get("payment_intent")
        except Exception as e:
            return False, f"Couldn't find the Stripe payment: {e}"
    if not pi:
        return False, "No Stripe payment intent on this order."
    try:
        _stripe_post("/refunds", {"payment_intent": pi})
        return True, "Refunded at Stripe."
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())["error"]["message"]
        except Exception:
            detail = f"HTTP {e.code}"
        # Already refunded is not a failure — carry on and void the tickets.
        if "already been refunded" in detail.lower():
            return True, "Already refunded at Stripe."
        return False, detail
    except Exception as e:
        return False, str(e)


def _stripe_get(path):
    req = urllib.request.Request(
        f"https://api.stripe.com/v1{path}",
        headers={"Authorization": f"Bearer {stripe_key()}",
                 "User-Agent": "MayhemBingoTickets/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def session_is_paid(session_id):
    """Check a Stripe Checkout Session's payment status (live mode only)."""
    if not is_live() or not session_id:
        return False
    try:
        session = _stripe_get(f"/checkout/sessions/{session_id}")
    except urllib.error.URLError:
        return False
    return session.get("payment_status") == "paid"
