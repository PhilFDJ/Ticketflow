"""End-to-end test: boots the real server and drives the full flow over HTTP."""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(__file__))
PORT = "8753"
BASE = f"http://127.0.0.1:{PORT}"


def start_server(dbpath):
    env = dict(os.environ)
    env["PORT"] = PORT
    env["TICKETFLOW_DB"] = dbpath
    env["ADMIN_PASSWORD"] = "secretpw"
    env.pop("STRIPE_SECRET_KEY", None)  # force mock mode
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "run.py")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            return p
        except Exception:
            time.sleep(0.1)
    out, err = p.communicate(timeout=2)
    raise RuntimeError("server did not start: " + err.decode()[:500])


def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                       NoRedirect())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # don't auto-follow; we assert on Location


def get(op, path):
    try:
        r = op.open(BASE + path, timeout=5)
        return r.getcode(), r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def post(op, path, data, json_body=False):
    if json_body:
        body = data.encode()
        headers = {"Content-Type": "application/json"}
    else:
        body = urllib.parse.urlencode(data).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        r = op.open(req, timeout=5)
        return r.getcode(), r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return cond


def run():
    dbfd, dbpath = tempfile.mkstemp(suffix=".db")
    os.close(dbfd); os.remove(dbpath)
    proc = start_server(dbpath)
    ok = True
    try:
        op = client()

        # 1. Home lists seeded events
        code, body, _ = get(op, "/")
        ok &= check("home 200 + has events", code == 200 and "Upcoming events" in body)
        ok &= check("home in mock mode banner", "Mock payment mode" in body)

        # find an event id + its ticket type ids from the event page
        eid = re.search(r"/events/(evt_\w+)", body).group(1)
        code, ev, _ = get(op, f"/events/{eid}")
        ok &= check("event page 200", code == 200)
        tt_ids = re.findall(r'name="qty_(tt_\w+)"', ev)
        ok &= check("event has ticket types", len(tt_ids) >= 1)

        # 2. Checkout -> mock pay -> confirm -> success with tickets
        form = {"event_id": eid, "buyer_name": "Test Buyer",
                "buyer_email": "buyer@test.com", "buyer_phone": "07700900123",
                f"qty_{tt_ids[0]}": "2"}
        code, _, hdrs = post(op, "/checkout", form)
        loc = hdrs.get("Location", "")
        ok &= check("checkout redirects to mock pay", code == 303 and "/mock/pay?order=" in loc)
        oid = loc.split("order=")[1]

        code, _, hdrs = post(op, "/mock/confirm", {"order": oid})
        ok &= check("mock confirm redirects to success", "/checkout/success?order=" in hdrs.get("Location", ""))

        code, succ, _ = get(op, f"/checkout/success?order={oid}")
        codes = list(dict.fromkeys(re.findall(r"(TKT-[A-Z0-9]+)", succ)))  # unique, ordered
        ok &= check("success shows 2 tickets", code == 200 and len(codes) == 2)
        ok &= check("success has inline QR svg", succ.count("<svg") >= 2)

        tcode = codes[0]

        # 3. Ticket page renders
        code, tp, _ = get(op, f"/t/{tcode}")
        ok &= check("ticket page 200 + svg", code == 200 and "<svg" in tp)

        # 4. Scan is ADMIN-ONLY. Prove it's locked, then log in and use it.
        code_unauth, _, _ = post(op, "/api/scan", json.dumps({"code": tcode}),
                                 json_body=True)
        ok &= check("scan API rejects anonymous (401)", code_unauth == 401)

        post(op, "/admin/login", {"password": "secretpw"})

        _, j1, _ = post(op, "/api/scan", json.dumps({"code": tcode}), json_body=True)
        _, j2, _ = post(op, "/api/scan", json.dumps({"code": tcode}), json_body=True)
        _, j3, _ = post(op, "/api/scan", json.dumps({"code": "TKT-BOGUS"}), json_body=True)
        ok &= check("scan first = ok", json.loads(j1)["status"] == "ok")
        ok &= check("scan second = already", json.loads(j2)["status"] == "already")
        ok &= check("scan bogus = invalid", json.loads(j3)["status"] == "invalid")
        # accepts full URL form too (new ticket)
        tcode2 = codes[1]
        _, j4, _ = post(op, "/api/scan", json.dumps({"code": f"{BASE}/t/{tcode2}"}), json_body=True)
        ok &= check("scan accepts URL form", json.loads(j4)["status"] == "ok")

        # 5. Stock decremented: buying more than remaining should fail gracefully
        #    (find remaining by hammering a tiny-stock type is overkill; just ensure
        #     oversell guard triggers with an absurd qty via direct order)

        # 6. Admin auth. Log out first — the scan test above signed us in.
        get(op, "/admin/logout")
        code, _, hdrs = get(op, "/admin")
        ok &= check("admin requires login (redirect)", code == 303 and "/admin/login" in hdrs.get("Location", ""))
        code, _, _ = post(op, "/admin/login", {"password": "wrong"})
        ok &= check("bad password rejected", code == 401)
        code, _, hdrs = post(op, "/admin/login", {"password": "secretpw"})
        ok &= check("good password sets session", code == 303)
        code, dash, _ = get(op, "/admin")
        ok &= check("dashboard loads", code == 200 and "Dashboard" in dash)
        ok &= check("dashboard shows revenue", "Total revenue" in dash)

        # 7. Create an event via dashboard
        newform = {
            "title": "QA Night", "venue": "Test Hall", "description": "desc",
            "starts_at": "2030-01-01T20:00", "currency": "GBP",
            "image_url": "#123456", "tt_name_0": "Standard",
            "tt_price_0": "12.50", "tt_qty_0": "5",
        }
        code, _, hdrs = post(op, "/admin/events/new", newform)
        newloc = hdrs.get("Location", "")
        ok &= check("event created -> redirect to event", "/admin/events/evt_" in newloc)
        code, mgr, _ = get(op, newloc)
        ok &= check("new event mgmt page", code == 200 and "QA Night" in mgr and "Standard" in mgr)

        # new event visible on public home
        code, home2, _ = get(op, "/")
        ok &= check("new event public", "QA Night" in home2)

        # 8. Oversell guard on the new event (only 5 in stock)
        code, evpage, _ = get(op, newloc.replace("/admin/events/", "/events/"))
        qid = re.search(r'name="qty_(tt_\w+)"', evpage).group(1)
        code, resp, _ = post(op, "/checkout", {
            "event_id": newloc.split("/")[-1], "buyer_name": "Greedy",
            "buyer_email": "g@t.com", "buyer_phone": "07700900123",
            f"qty_{qid}": "99"})
        ok &= check("oversell blocked", "Only 5 left" in resp or "left for" in resp)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(dbpath):
            os.remove(dbpath)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
