"""Payment handling: Stripe Checkout (test mode) with a built-in mock fallback.

No third-party SDK — talks to the Stripe REST API with urllib, so the only
requirement to go live with test payments is a Stripe test secret key in the
environment. With no key set, the app uses a self-contained mock checkout so it
runs end-to-end out of the box.
"""
import json
import os
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


def session_is_paid(session_id):
    """Check a Stripe Checkout Session's payment status (live mode only)."""
    if not is_live() or not session_id:
        return False
    try:
        session = _stripe_get(f"/checkout/sessions/{session_id}")
    except urllib.error.URLError:
        return False
    return session.get("payment_status") == "paid"
