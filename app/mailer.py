"""Ticket email delivery — stdlib only, no SDK.

Supports two providers, chosen by environment:

  RESEND_API_KEY  → send via Resend's HTTP API (you already use this elsewhere)
  SMTP_HOST/...   → send via any SMTP server

If neither is configured, sending is a no-op that reports False, and the buyer
still sees their tickets on screen. Email must NEVER be able to fail a sale, so
every call site treats a failure as non-fatal.
"""
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage


def _resend_key():
    return os.environ.get("RESEND_API_KEY", "").strip()


def _smtp_host():
    return os.environ.get("SMTP_HOST", "").strip()


def is_configured() -> bool:
    return bool(_resend_key() or _smtp_host())


def from_address() -> str:
    """Sender. The domain MUST be verified with your mail provider or sending
    fails. `mayhembingo.co.uk` is only a brand — the verified domain is
    phil-freeman.co.uk, so we send from there but display the Mayhem Bingo name,
    which is what the buyer actually sees in their inbox.
    """
    return os.environ.get("MAIL_FROM", "Mayhem Bingo <tickets@phil-freeman.co.uk>").strip()


def send(to_email: str, subject: str, html: str, text: str = "") -> bool:
    """Send one email. Returns True on success, False on any failure.

    Never raises — a failed email must not break a completed purchase.
    """
    to_email = (to_email or "").strip()
    if not to_email or not is_configured():
        return False
    try:
        if _resend_key():
            return _send_resend(to_email, subject, html, text)
        return _send_smtp(to_email, subject, html, text)
    except Exception as e:  # noqa: BLE001 - deliberately swallow everything
        print(f"[mailer] send failed to {to_email}: {e}")
        return False


def _send_resend(to_email, subject, html, text) -> bool:
    payload = {
        "from": from_address(),
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {_resend_key()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        ok = 200 <= resp.status < 300
        if not ok:
            print(f"[mailer] resend returned {resp.status}")
        return ok


def _send_smtp(to_email, subject, html, text) -> bool:
    host = _smtp_host()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address()
    msg["To"] = to_email
    msg.set_content(text or "Your tickets are attached — open the link to view them.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        if user:
            s.login(user, pwd)
        s.send_message(msg)
    return True


# ---------------------------------------------------------------------------
# The ticket email itself
# ---------------------------------------------------------------------------
def ticket_email_html(event, tickets, base_url, qr_svgs=None):
    """Build the buyer's ticket email.

    QR codes are inline SVG, which many mail clients (notably Gmail) strip. So
    the email leads with a big, reliable LINK to each ticket, and shows the code
    as text as a final fallback. The QR is a bonus where it renders, never the
    only way in.
    """
    rows = []
    for t in tickets:
        url = f"{base_url}/t/{t['code']}"
        rows.append(f"""
        <table role="presentation" width="100%" style="border:1px solid #d9e0ea;border-radius:12px;margin:0 0 14px">
          <tr><td style="padding:18px 20px;font-family:Helvetica,Arial,sans-serif">
            <div style="font-size:13px;color:#5a6b7b">{_e(event['title'])}</div>
            <div style="font-size:17px;font-weight:700;color:#0f1720;margin:2px 0 10px">{_e(t['ticket_name'])}</div>
            <a href="{_e(url)}" style="display:inline-block;background:#6366f1;color:#fff;
               text-decoration:none;font-weight:600;font-size:15px;padding:12px 20px;border-radius:9px">
               View &amp; show this ticket</a>
            <div style="font-size:12px;color:#5a6b7b;margin-top:12px">
              Ticket code: <b style="letter-spacing:.04em">{_e(t['code'])}</b>
            </div>
          </td></tr>
        </table>""")

    logo = f'{base_url}/static/logo.png'
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f7fa">
  <div style="max-width:560px;margin:0 auto;font-family:Helvetica,Arial,sans-serif;color:#22303c">
    <div style="text-align:center;margin-bottom:18px">
      <img src="{_e(logo)}" alt="Mayhem Bingo" width="200"
           style="max-width:200px;height:auto;display:inline-block">
    </div>
    <h1 style="font-size:22px;margin:0 0 6px;color:#0f1720">You're in! 🎉</h1>
    <p style="margin:0 0 4px;font-size:15px"><b>{_e(event['title'])}</b></p>
    <p style="margin:0 0 18px;color:#5a6b7b;font-size:14px">
      {_e(event.get('venue') or '')}
    </p>
    <p style="font-size:14px;margin:0 0 18px">
      Here {'are your tickets' if len(tickets) != 1 else 'is your ticket'} — tap to open,
      then show the QR code at the door. You can reopen this any time from this email.
    </p>
    {''.join(rows)}
    <p style="font-size:12px;color:#5a6b7b;margin-top:20px">
      Keep this email safe — it's your ticket. If the QR won't scan on the night,
      the ticket code above can be entered by hand at the door.
    </p>
  </div>
</body></html>"""


def ticket_email_text(event, tickets, base_url):
    lines = [f"You're in! — {event['title']}", ""]
    if event.get("venue"):
        lines.append(event["venue"])
        lines.append("")
    lines.append("Your tickets:")
    for t in tickets:
        lines.append(f"  {t['ticket_name']}: {base_url}/t/{t['code']}")
        lines.append(f"    code: {t['code']}")
    lines += ["", "Show the QR at the door. Keep this email — it's your ticket."]
    return "\n".join(lines)


def _e(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
