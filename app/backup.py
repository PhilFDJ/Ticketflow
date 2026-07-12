"""Automatic backups.

THE HONEST PROBLEM
Everything — orders, customers, stock, takings — lives in one SQLite file on a
Render disk that has no automatic backup on the starter plan. Lose the disk, lose
the business. The manual "download" button only helps if you remember to press it,
which nobody reliably does.

WHAT THIS DOES
Two layers, because they protect against different disasters:

  1. On-disk snapshots (/var/data/backups). Protects against the LIKELY disaster:
     you delete the wrong event, or a bad change corrupts something. Instant,
     free, keeps the last N. Does NOT protect you if the disk itself dies.

  2. Emailed off-site copy. Protects against the disk dying, which is the one that
     actually ends you. Uses the Resend account that's already set up and working,
     so there's nothing new to configure and nothing to pay for.

A backup that lives on the same disk as the original is not really a backup. That's
why layer 2 exists, and why the app tells you plainly if it isn't running.
"""

import base64
import os
import sqlite3
import tempfile
import threading
import time

import db
import mailer


BACKUP_DIR = os.environ.get("TICKETFLOW_BACKUPS", "/var/data/backups")
KEEP = int(os.environ.get("BACKUP_KEEP", "14"))          # nightly, ~2 weeks
INTERVAL = int(os.environ.get("BACKUP_INTERVAL_HOURS", "24")) * 3600
EMAIL_EVERY = int(os.environ.get("BACKUP_EMAIL_HOURS", "24")) * 3600

# Resend rejects big attachments; well under its limit, and a signal to move to
# Postgres if we ever get near it.
MAX_EMAIL_BYTES = 20 * 1024 * 1024


def _snapshot_bytes():
    """A consistent copy of the DB, safe to take while the site is taking orders.

    Uses SQLite's backup API rather than copying the file — a plain file copy of a
    database mid-write can be corrupt, which would make the backup worthless
    precisely when you need it.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        src = sqlite3.connect(db.DB_PATH)
        dst = sqlite3.connect(tmp.name)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        with open(tmp.name, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def write_snapshot():
    """Save a snapshot to disk and prune old ones. Returns (path, size)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    data = _snapshot_bytes()
    # Seconds, not just minutes: two snapshots in the same minute (a manual one
    # right after an automatic one) would otherwise overwrite each other, silently
    # leaving you with fewer backups than you think you have.
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"mayhem-{stamp}.db")
    with open(path, "wb") as f:
        f.write(data)

    # Keep the most recent N; a disk full of backups is its own outage.
    snaps = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")),
        reverse=True)
    for old in snaps[KEEP:]:
        try:
            os.unlink(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass

    return path, len(data)


def list_snapshots():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not f.endswith(".db"):
            continue
        p = os.path.join(BACKUP_DIR, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": f, "path": p, "size": st.st_size,
                    "when": int(st.st_mtime)})
    return out


def backup_email_to():
    return (os.environ.get("BACKUP_EMAIL")
            or os.environ.get("ALERT_EMAIL")
            or mailer.reply_to() or "").strip()


def email_snapshot():
    """Send the database off-site. Returns (ok, message).

    This is the layer that actually saves you if the Render disk dies — an on-disk
    snapshot would die with it.
    """
    to = backup_email_to()
    if not to:
        return False, "No BACKUP_EMAIL set."
    if not mailer.is_configured():
        return False, "Email isn't configured."

    data = _snapshot_bytes()
    if len(data) > MAX_EMAIL_BYTES:
        return False, (f"Database is {len(data)/1e6:.1f}MB — too big to email. "
                       f"Time to move to Postgres.")

    stamp = time.strftime("%d %b %Y, %H:%M")
    fname = f"mayhem-tickets-{time.strftime('%Y-%m-%d')}.db"
    stats = _summary()

    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:520px">
      <h2 style="margin:0 0 4px">Backup — {stamp}</h2>
      <p style="color:#5a6b7b;margin:0 0 16px">
        Attached is a complete copy of the Mayhem Bingo tickets database.</p>
      <table style="font-size:14px;border-collapse:collapse">
        <tr><td style="padding:3px 14px 3px 0;color:#5a6b7b">Events</td>
            <td><b>{stats['events']}</b></td></tr>
        <tr><td style="padding:3px 14px 3px 0;color:#5a6b7b">Paid orders</td>
            <td><b>{stats['orders']}</b></td></tr>
        <tr><td style="padding:3px 14px 3px 0;color:#5a6b7b">Tickets</td>
            <td><b>{stats['tickets']}</b></td></tr>
        <tr><td style="padding:3px 14px 3px 0;color:#5a6b7b">Taken</td>
            <td><b>£{stats['revenue']/100:,.2f}</b></td></tr>
        <tr><td style="padding:3px 14px 3px 0;color:#5a6b7b">File</td>
            <td>{len(data)/1024:.0f} KB</td></tr>
      </table>
      <p style="font-size:12px;color:#8b9bab;margin-top:18px">
        <b>Keep this email.</b> It's your off-site copy — if the server disk fails,
        this attachment is how you get everything back. To restore, put the file on
        the server at <code>/var/data/ticketflow.db</code> and restart.
      </p>
    </div>"""
    text = (f"Backup — {stamp}\n\n{stats['events']} events, {stats['orders']} paid "
            f"orders, {stats['tickets']} tickets, £{stats['revenue']/100:,.2f} taken.\n\n"
            f"Keep this email — the attachment is your off-site copy.\n")

    ok, err = mailer.send_verbose(
        to, f"Mayhem Bingo tickets — backup {time.strftime('%d %b')}",
        html, text,
        attachments=[{
            "filename": fname,
            "content": base64.b64encode(data).decode(),
        }])
    if not ok:
        return False, err or "Send failed."
    return True, f"Backup emailed to {to}."


def _summary():
    with db.cursor() as conn:
        ev = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        od = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(total),0) v FROM orders "
            "WHERE status = 'paid'").fetchone()
        tk = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
    return {"events": ev, "orders": od["c"], "revenue": od["v"], "tickets": tk}


# ---------------------------------------------------------------------------
# The scheduler
# ---------------------------------------------------------------------------
_state = {"last_snapshot": 0, "last_email": 0,
          "last_error": "", "running": False}


def status():
    snaps = list_snapshots()
    return {
        "running": _state["running"],
        "snapshots": len(snaps),
        "latest": snaps[0] if snaps else None,
        "last_email": _state["last_email"],
        "last_error": _state["last_error"],
        "email_to": backup_email_to(),
        "email_on": bool(backup_email_to()) and mailer.is_configured(),
    }


def _loop():
    # Don't fire the moment the app boots — a deploy would trigger a backup and an
    # email every single time. Settle first.
    time.sleep(120)
    while True:
        try:
            now = time.time()
            if now - _state["last_snapshot"] >= INTERVAL:
                path, size = write_snapshot()
                _state["last_snapshot"] = now
                print(f"[backup] snapshot {path} ({size/1024:.0f} KB)")

            if now - _state["last_email"] >= EMAIL_EVERY and backup_email_to():
                ok, msg = email_snapshot()
                _state["last_email"] = now
                if ok:
                    print(f"[backup] {msg}")
                    _state["last_error"] = ""
                else:
                    print(f"[backup] email FAILED: {msg}")
                    _state["last_error"] = msg
        except Exception as e:
            _state["last_error"] = str(e)
            print(f"[backup] error: {e}")
        time.sleep(600)          # check every 10 min; the interval gates the work


def start():
    """Kick off the background backup thread."""
    if _state["running"]:
        return
    _state["running"] = True
    t = threading.Thread(target=_loop, daemon=True, name="backup")
    t.start()
    print(f"[backup] running — snapshot every {INTERVAL//3600}h, "
          f"email {'to ' + backup_email_to() if backup_email_to() else 'OFF'}")
