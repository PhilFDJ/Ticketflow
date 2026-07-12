"""ID, ticket-code, and signed-session token helpers (stdlib only)."""
import base64
import hashlib
import hmac
import secrets
import time


def new_id(prefix: str) -> str:
    """Short, URL-safe, sortable-ish unique id, e.g. 'evt_a1b2c3d4e5f6'."""
    return f"{prefix}_{secrets.token_hex(6)}"


def ticket_code() -> str:
    """Public code encoded in a ticket QR. Short + unambiguous → small QR.

    Format: 'TKT-' + 26-char Base32 (no padding). Verified scannable in
    tests/test_qr.py.
    """
    raw = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
    return "TKT-" + raw


# ---------------------------------------------------------------------------
# Signed session cookies for the organiser dashboard.
# HMAC over "issued_at" keeps things stateless; the secret is derived from the
# admin password so changing the password invalidates old sessions.
# ---------------------------------------------------------------------------
def _secret(admin_password: str) -> bytes:
    return hashlib.sha256(("ticketflow:" + admin_password).encode()).digest()


def make_session(admin_password: str, ttl_seconds: int = 60 * 60 * 12) -> str:
    exp = int(time.time()) + ttl_seconds
    msg = str(exp).encode()
    sig = hmac.new(_secret(admin_password), msg, hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_session(token: str, admin_password: str) -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    expected = hmac.new(_secret(admin_password), exp_str.encode(),
                        hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)
