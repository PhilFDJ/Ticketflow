"""Apple Wallet (.pkpass) ticket generation — stdlib + openssl only.

HOW A .pkpass WORKS
A pass is a ZIP containing:
  pass.json    — the ticket's content and layout
  manifest.json— SHA-1 of every other file
  signature    — a detached PKCS#7 signature of manifest.json
  icon.png, logo.png … — artwork

The signature is the whole game: iPhones reject any pass that isn't signed with a
certificate issued by Apple for YOUR Pass Type ID. There is no way around this and
no free tier — it needs a paid Apple Developer account.

WHAT YOU MUST PROVIDE (as env vars on Render)
  APPLE_PASS_TYPE_ID    e.g. pass.co.uk.mayhembingo.ticket
  APPLE_TEAM_ID         your 10-character Apple Team ID
  APPLE_PASS_CERT       the pass certificate + private key, PEM, base64-encoded
  APPLE_WWDR_CERT       Apple's WWDR intermediate cert, PEM, base64-encoded

If any are missing, wallet passes are simply disabled and the button is hidden —
the ticket still works by QR, link and print, exactly as before.
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import zipfile


def _env(name):
    return os.environ.get(name, "").strip()


def pass_type_id():
    return _env("APPLE_PASS_TYPE_ID")


def team_id():
    return _env("APPLE_TEAM_ID")


def is_configured() -> bool:
    """Only offer the Wallet button if we can actually sign a pass."""
    return bool(pass_type_id() and team_id()
                and _env("APPLE_PASS_CERT") and _env("APPLE_WWDR_CERT"))


def config_problem() -> str:
    """Human-readable reason wallet passes are off (for the admin panel)."""
    missing = [k for k in ("APPLE_PASS_TYPE_ID", "APPLE_TEAM_ID",
                           "APPLE_PASS_CERT", "APPLE_WWDR_CERT") if not _env(k)]
    if not missing:
        return ""
    return "Not set: " + ", ".join(missing)


def _decode_pem(env_name):
    """Certs are stored base64-encoded so they survive a single-line env var."""
    raw = _env(env_name)
    try:
        data = base64.b64decode(raw, validate=True)
        # If it decoded to something that looks like PEM, use it.
        if b"-----BEGIN" in data:
            return data
    except Exception:
        pass
    # Allow a raw PEM to be pasted directly too.
    return raw.encode()


# ---------------------------------------------------------------------------
# Artwork. Apple requires at least an icon; we generate simple PNGs from the
# site logo so there's nothing extra to deploy.
# ---------------------------------------------------------------------------
def _read_static(name):
    path = os.path.join(os.path.dirname(__file__), "static", name)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def _artwork():
    """Apple validates artwork sizes strictly: icon.png must be 29x29 (58x58 for
    @2x), logo.png at most 160x50. Wrong sizes get the whole pass rejected — which
    surfaces to the buyer as Safari refusing to open the file at all.

    These are PRE-GENERATED at the correct sizes and shipped as static files, so
    the server needs no image library (the app installs nothing on deploy).
    """
    files = {}
    mapping = {
        "icon.png":    "pass-icon.png",
        "icon@2x.png": "pass-icon@2x.png",
        "logo.png":    "pass-logo.png",
        "logo@2x.png": "pass-logo@2x.png",
    }
    for pass_name, static_name in mapping.items():
        data = _read_static(static_name)
        if data:
            files[pass_name] = data
    return files


def build_pass(ticket, event, base_url) -> bytes:
    """Build a signed .pkpass for one ticket. Raises on failure."""
    if not is_configured():
        raise RuntimeError("Apple Wallet is not configured.")

    serial = ticket["code"]

    # eventTicket layout. The barcode carries the same code the door scanner
    # reads, so a Wallet pass and an emailed QR are interchangeable.
    pass_json = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_id(),
        "teamIdentifier": team_id(),
        "organizationName": "Mayhem Bingo",
        "description": f"{event['title']} ticket",
        "serialNumber": serial,
        "backgroundColor": "rgb(17, 26, 46)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(154, 168, 196)",
        "barcodes": [{
            "format": "PKBarcodeFormatQR",
            "message": f"{base_url}/t/{serial}",
            "messageEncoding": "iso-8859-1",
            "altText": serial,
        }],
        "eventTicket": {
            "primaryFields": [
                {"key": "event", "label": "EVENT", "value": event["title"]},
            ],
            "secondaryFields": [
                {"key": "venue", "label": "VENUE", "value": event.get("venue") or ""},
                {"key": "type", "label": "TICKET", "value": ticket.get("ticket_name") or ""},
            ],
            "auxiliaryFields": [
                {
                    "key": "doors",
                    "label": "DOORS",
                    "value": _iso(event["starts_at"]),
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                },
            ],
            "backFields": [
                {"key": "code", "label": "Ticket code", "value": serial},
                {"key": "address", "label": "Venue",
                 "value": _venue_block(event)},
                {"key": "link", "label": "View ticket",
                 "value": f"{base_url}/t/{serial}"},
                {"key": "terms", "label": "Info",
                 "value": "Show this pass at the door. One admission per pass."},
            ],
        },
        # Show the pass on the lock screen near the venue/time.
        "relevantDate": _iso(event["starts_at"]),
    }

    files = {"pass.json": json.dumps(pass_json).encode()}
    files.update(_artwork())

    # manifest = SHA-1 of every file
    manifest = {name: hashlib.sha1(data).hexdigest() for name, data in files.items()}
    manifest_bytes = json.dumps(manifest).encode()

    signature = _sign(manifest_bytes)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("manifest.json", manifest_bytes)
        z.writestr("signature", signature)
    return buf.getvalue()


def _venue_block(event):
    """Venue name + address for the back of the pass. Apple Wallet linkifies an
    address automatically, so tapping it opens Apple Maps."""
    name = (event.get("venue") or "").strip()
    addr = ""
    try:
        addr = (event["address"] or "").strip()
    except (KeyError, TypeError):
        addr = ""
    parts = [p for p in (name, addr) if p]
    return "\n".join(parts) if parts else "See the event page for details."


def _iso(ts):
    """ISO 8601 as Apple wants it: 2026-09-04T19:00:00+01:00

    Note the COLON in the timezone offset. Python's %z gives '+0100' with no
    colon, which Apple rejects — and a rejected pass shows up to the buyer as
    Safari refusing to open the file.
    """
    import time
    lt = time.localtime(int(ts))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", lt)
    off = time.strftime("%z", lt)          # e.g. +0100
    if len(off) == 5:                      # insert the colon: +01:00
        off = off[:3] + ":" + off[3:]
    return stamp + (off or "+00:00")


def _sign(manifest_bytes: bytes) -> bytes:
    """Detached PKCS#7 signature of manifest.json, as Apple requires.

    Uses openssl rather than a Python crypto library so the app stays
    dependency-free (no pip install on the server).
    """
    tmp = tempfile.mkdtemp(prefix="pkpass_")
    try:
        cert_path = os.path.join(tmp, "cert.pem")
        wwdr_path = os.path.join(tmp, "wwdr.pem")
        man_path = os.path.join(tmp, "manifest.json")
        sig_path = os.path.join(tmp, "signature")

        with open(cert_path, "wb") as f:
            f.write(_decode_pem("APPLE_PASS_CERT"))
        with open(wwdr_path, "wb") as f:
            f.write(_decode_pem("APPLE_WWDR_CERT"))
        with open(man_path, "wb") as f:
            f.write(manifest_bytes)

        cmd = [
            "openssl", "smime", "-binary", "-sign",
            "-certfile", wwdr_path,       # Apple's WWDR intermediate
            "-signer", cert_path,         # your Pass Type ID cert
            "-inkey", cert_path,          # ...and its private key (same PEM)
            "-in", man_path,
            "-out", sig_path,
            "-outform", "DER",
            # NOTE: `smime -sign` produces a DETACHED signature by default, which
            # is exactly what Apple requires. Do not pass -nodetach.
        ]
        # The pass cert may be passphrase-protected.
        passphrase = _env("APPLE_PASS_PASSWORD")
        if passphrase:
            cmd += ["-passin", f"pass:{passphrase}"]

        proc = subprocess.run(cmd, capture_output=True, timeout=20)
        if proc.returncode != 0 or not os.path.isfile(sig_path):
            raise RuntimeError(
                "openssl failed to sign the pass: "
                + proc.stderr.decode()[:300]
            )
        with open(sig_path, "rb") as f:
            return f.read()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
