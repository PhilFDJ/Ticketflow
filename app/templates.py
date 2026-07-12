"""HTML rendering for TicketFlow. Pure string templating, stdlib only."""
import html
import time

CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(pence, currency="GBP"):
    sym = CURRENCY_SYMBOL.get(currency, "")
    return f"{sym}{pence / 100:,.2f}"


def fmt_date(ts, with_time=True):
    t = time.localtime(ts)
    s = time.strftime("%a %-d %b %Y", t) if hasattr(time, "strftime") else str(ts)
    try:
        s = time.strftime("%a %-d %b %Y", t)
        if with_time:
            s += time.strftime(" · %-I:%M %p", t)
    except ValueError:  # platforms without %-d
        s = time.strftime("%a %d %b %Y", t)
        if with_time:
            s += time.strftime(" · %I:%M %p", t)
    return s


def date_badge(ts):
    t = time.localtime(ts)
    return time.strftime("%d", t), time.strftime("%b", t).upper()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def layout(title, body, active="", admin=False):
    nav = (
        f'<a href="/" class="{ "active" if active=="home" else "" }">Events</a>'
        f'<a href="/scan">Scan</a>'
    )
    if admin:
        nav += '<a href="/admin">Dashboard</a><a href="/admin/logout">Sign out</a>'
    else:
        nav += '<a href="/admin">Organiser</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · TicketFlow</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="site"><div class="container">
  <a class="brand" href="/"><span class="logo">◆</span> TicketFlow</a>
  <nav class="nav">{nav}</nav>
</div></header>
<main><div class="container">
{body}
</div></main>
<footer class="site"><div class="container">
  TicketFlow — a self-hosted ticketing MVP. Prices in test mode.
</div></footer>
</body>
</html>"""


def flash(kind, msg):
    return f'<div class="flash {kind}">{esc(msg)}</div>'


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
def home(events, ticket_types_by_event, live_mode):
    banner = ("" if live_mode else
              '<div class="banner">💳 <b>Mock payment mode</b> — no Stripe key set, '
              'so checkout is simulated and no real card is charged. '
              'Add a Stripe test key to enable real test-mode payments.</div>')
    cards = []
    for e in events:
        tts = ticket_types_by_event.get(e["id"], [])
        prices = [t["price"] for t in tts if (t["quantity"] - t["sold"]) > 0]
        price_label = ("From " + money(min(prices), e["currency"])) if prices else "Sold out"
        d, m = date_badge(e["starts_at"])
        cover = e["image_url"] if e["image_url"].startswith("#") else "#4f46e5"
        cards.append(f"""
        <a class="card event-card" href="/events/{esc(e['id'])}">
          <div class="event-cover" style="background:{esc(cover)}">
            <div class="date"><div class="d">{d}</div><div class="m">{m}</div></div>
          </div>
          <div class="body">
            <div class="title">{esc(e['title'])}</div>
            <div class="venue">{esc(e['venue'])}</div>
            <div class="price"><span class="pill">{esc(price_label)}</span></div>
          </div>
        </a>""")
    if not cards:
        grid = ('<div class="card"><div class="body center muted">No events yet. '
                '<a href="/admin">Create one in the dashboard →</a></div></div>')
    else:
        grid = f'<div class="grid cols-3">{"".join(cards)}</div>'
    return layout("Events", f"""
    {banner}
    <h1>Upcoming events</h1>
    <p class="lead">Find your next night out and grab tickets in seconds.</p>
    {grid}
    """, active="home")


def event_detail(event, ticket_types, live_mode, error=None):
    d = fmt_date(event["starts_at"])
    cover = event["image_url"] if event["image_url"].startswith("#") else "#4f46e5"
    rows = []
    any_available = False
    for t in ticket_types:
        remaining = t["quantity"] - t["sold"]
        avail = remaining > 0
        any_available = any_available or avail
        control = (f"""
          <div class="stepper" data-price="{t['price']}">
            <button type="button" onclick="step('{t['id']}',-1)">−</button>
            <input id="q_{t['id']}" name="qty_{t['id']}" value="0" readonly>
            <button type="button" onclick="step('{t['id']}',1)" data-max="{remaining}">+</button>
          </div>""" if avail else '<span class="pill bad">Sold out</span>')
        rows.append(f"""
          <div class="tt-row">
            <div>
              <h3>{esc(t['name'])}</h3>
              <div class="muted small">{money(t['price'], event['currency'])}
                · {remaining} left</div>
            </div>
            <div>{control}</div>
          </div>""")
    err = flash("err", error) if error else ""
    buy = f"""
      <form method="post" action="/checkout" id="buyform">
        <input type="hidden" name="event_id" value="{esc(event['id'])}">
        {''.join(rows)}
        <div class="row mt2">
          <div><label>Your name</label>
            <input name="buyer_name" required placeholder="Alex Smith"></div>
          <div><label>Email</label>
            <input name="buyer_email" type="email" required placeholder="alex@email.com"></div>
        </div>
        <div class="mt3" style="display:flex;align-items:center;justify-content:space-between">
          <div class="muted">Total <span id="total" style="color:var(--ink);font-size:20px;font-weight:700">{money(0, event['currency'])}</span></div>
          <button class="btn" id="checkoutbtn" type="submit" disabled>Checkout →</button>
        </div>
      </form>""" if any_available else '<div class="flash info">This event is sold out.</div>'

    body = f"""
    <a href="/" class="muted small">← All events</a>
    <div class="card mt2" style="overflow:hidden">
      <div class="event-cover" style="height:150px;background:{esc(cover)}"></div>
      <div class="body">
        <span class="pill">{esc(d)}</span>
        <h1 class="mt2">{esc(event['title'])}</h1>
        <p class="lead">{esc(event['venue'])}</p>
        <p>{esc(event['description'])}</p>
      </div>
    </div>
    <div class="card mt3"><div class="body">
      <h2 class="mt0">Tickets</h2>
      {err}
      {buy}
    </div></div>
    <script>
      const cur = {{"GBP":"£","USD":"$","EUR":"€"}}["{event['currency']}"]||"";
      function fmt(p){{return cur + (p/100).toFixed(2);}}
      function step(id, delta){{
        const inp = document.getElementById('q_'+id);
        const plus = inp.nextElementSibling;
        const max = parseInt(plus.getAttribute('data-max'));
        let v = parseInt(inp.value||'0') + delta;
        v = Math.max(0, Math.min(max, v));
        inp.value = v; recompute();
      }}
      function recompute(){{
        let total = 0, count = 0;
        document.querySelectorAll('.stepper').forEach(s=>{{
          const price = parseInt(s.getAttribute('data-price'));
          const q = parseInt(s.querySelector('input').value||'0');
          total += price*q; count += q;
        }});
        document.getElementById('total').textContent = fmt(total);
        const btn = document.getElementById('checkoutbtn');
        if(btn) btn.disabled = count===0;
      }}
    </script>"""
    return layout(event["title"], body)


def mock_pay(order, event, base_url):
    return layout("Payment", f"""
    <div class="narrow" style="margin:0 auto">
      <div class="banner">🔒 <b>Mock checkout</b> — this simulates a card payment.
        No real charge is made. In live mode this screen is Stripe Checkout.</div>
      <div class="card"><div class="body">
        <h1 class="mt0">Pay {money(order['total'], order['currency'])}</h1>
        <p class="muted">{esc(event['title'])}</p>
        <label>Card number</label>
        <input value="4242 4242 4242 4242" readonly>
        <div class="row">
          <div><label>Expiry</label><input value="12 / 34" readonly></div>
          <div><label>CVC</label><input value="123" readonly></div>
        </div>
        <form method="post" action="/mock/confirm" class="mt3">
          <input type="hidden" name="order" value="{esc(order['id'])}">
          <button class="btn full" type="submit">Pay {money(order['total'], order['currency'])}</button>
        </form>
        <form method="post" action="/mock/cancel" class="mt2">
          <input type="hidden" name="order" value="{esc(order['id'])}">
          <input type="hidden" name="event" value="{esc(event['id'])}">
          <button class="btn ghost full" type="submit">Cancel</button>
        </form>
      </div></div>
    </div>""")


def success(order, event, tickets, qr_svgs):
    tks = []
    for t in tickets:
        tks.append(f"""
        <div class="card mt2"><div class="body center">
          <div class="pill ok">Valid ticket</div>
          <h3 class="mt2">{esc(t['ticket_name'])}</h3>
          <div class="muted small">{esc(event['title'])}</div>
          <div class="qr" style="background:#fff;padding:14px;border-radius:12px;width:min(240px,70%);margin:16px auto">{qr_svgs[t['code']]}</div>
          <div class="code">{esc(t['code'])}</div>
          <a class="btn ghost sm" href="/t/{esc(t['code'])}">Open full ticket</a>
        </div></div>""")
    return layout("You're in!", f"""
    <div class="narrow" style="margin:0 auto">
      <div class="center">
        <div class="pill ok">Payment successful</div>
        <h1 class="mt2">You're going! 🎉</h1>
        <p class="lead">{len(tickets)} ticket{'s' if len(tickets)!=1 else ''} for
          <b>{esc(event['title'])}</b>.<br>We've kept them here — show the QR at the door.</p>
        <p class="muted small">A confirmation would be emailed to {esc(order['buyer_email'])}
          in a production setup.</p>
      </div>
      {''.join(tks)}
      <div class="center mt3"><a href="/" class="muted">← Back to events</a></div>
    </div>""")


def ticket_page(t, qr_svg):
    return layout("Ticket", f"""
    <div class="ticket">
      <div class="top">
        <div class="pill {'ok' if t['status']=='valid' else 'warn'}">
          {'Valid' if t['status']=='valid' else 'Already used'}</div>
        <h2 class="mt2 mt0">{esc(t['event_title'])}</h2>
        <div class="muted small">{esc(fmt_date(t['event_starts_at']))}</div>
        <div class="muted small">{esc(t['event_venue'])}</div>
        <div class="mt2"><span class="pill">{esc(t['ticket_name'])}</span></div>
        <span class="notch l"></span><span class="notch r"></span>
      </div>
      <div class="qr">{qr_svg}</div>
      <div class="code">{esc(t['code'])}</div>
    </div>
    <div class="center mt3"><a href="/" class="muted">← All events</a></div>
    """)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scanner():
    body = """
    <div class="narrow" style="margin:0 auto">
      <h1>Door scanner</h1>
      <p class="lead">Point the camera at a ticket QR to check people in.</p>
      <div id="reader"></div>
      <div id="out"></div>
      <div class="center mt2">
        <button class="btn ghost" id="startbtn" onclick="startScan()">Start camera</button>
      </div>
      <p class="muted small center mt2">Camera needs a secure context. On desktop, localhost works.
        For a phone, run the server over your LAN with HTTPS or use the manual box below.</p>
      <div class="card mt3"><div class="body">
        <label>Or enter a code manually</label>
        <div class="row">
          <input id="manual" placeholder="TKT-XXXXXXXX">
          <button class="btn" style="flex:0 0 auto" onclick="check(document.getElementById('manual').value)">Check</button>
        </div>
      </div></div>
    </div>
    <script>
    let last = "", lastAt = 0, running = false;
    async function check(code){
      code = (code||"").trim(); if(!code) return;
      const now = Date.now();
      if(code===last && now-lastAt < 2500) return; // debounce repeats
      last = code; lastAt = now;
      const out = document.getElementById('out');
      try{
        const r = await fetch('/api/scan', {method:'POST',headers:{'Content-Type':'application/json'},
          body: JSON.stringify({code})});
        const j = await r.json();
        const cls = j.status==='ok'?'ok':(j.status==='already'?'already':'invalid');
        const head = j.status==='ok'?'✓ Admitted':(j.status==='already'?'⚠ Already used':'✕ Invalid ticket');
        let detail = '';
        if(j.ticket){ detail = `<div>${j.ticket.event_title}</div>
            <div class="muted small">${j.ticket.ticket_name} · ${j.ticket.buyer_name||''}</div>`;
          if(j.status==='already' && j.ticket.scanned_at)
            detail += `<div class="muted small">First scanned earlier</div>`;
        }
        out.innerHTML = `<div class="scan-result ${cls}"><div class="big">${head}</div>${detail}</div>`;
        if(navigator.vibrate) navigator.vibrate(j.status==='ok'?80:[60,40,60]);
      }catch(e){
        out.innerHTML = `<div class="scan-result invalid"><div class="big">Network error</div></div>`;
      }
    }
    async function startScan(){
      if(running) return;
      if(!('BarcodeDetector' in window)){
        document.getElementById('out').innerHTML =
          '<div class="scan-result already"><div class="big">Camera scanning unavailable</div>'+
          '<div class="muted small">This browser lacks BarcodeDetector. Use the manual box, '+
          'or try Chrome/Safari on a phone.</div></div>';
        return;
      }
      const det = new BarcodeDetector({formats:['qr_code']});
      const reader = document.getElementById('reader');
      const video = document.createElement('video');
      video.setAttribute('playsinline','');
      reader.innerHTML=''; reader.appendChild(video);
      let stream;
      try{
        stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
      }catch(e){
        document.getElementById('out').innerHTML =
          '<div class="scan-result invalid"><div class="big">Camera blocked</div>'+
          '<div class="muted small">Allow camera access and reload.</div></div>';
        return;
      }
      video.srcObject = stream; await video.play();
      running = true; document.getElementById('startbtn').textContent = 'Scanning…';
      const loop = async () => {
        if(!running) return;
        try{
          const codes = await det.detect(video);
          if(codes.length) check(codes[0].rawValue);
        }catch(e){}
        requestAnimationFrame(loop);
      };
      loop();
    }
    </script>"""
    return layout("Scanner", body, active="scan")


# ---------------------------------------------------------------------------
# Organiser dashboard
# ---------------------------------------------------------------------------
def admin_login(error=None):
    err = flash("err", error) if error else ""
    return layout("Organiser sign in", f"""
    <div class="narrow" style="margin:40px auto 0">
      <div class="card"><div class="body">
        <h1 class="mt0">Organiser sign in</h1>
        <p class="muted">Manage your events, tickets and door check-ins.</p>
        {err}
        <form method="post" action="/admin/login">
          <label>Password</label>
          <input name="password" type="password" autofocus required>
          <button class="btn full mt3" type="submit">Sign in</button>
        </form>
        <p class="muted small mt2">Default password is <code>admin123</code> —
          set <code>ADMIN_PASSWORD</code> to change it.</p>
      </div></div>
    </div>""")


def admin_dashboard(events, stats_by_event, live_mode):
    rows = []
    tot_rev = 0
    for e in events:
        s = stats_by_event[e["id"]]
        tot_rev += s["revenue"]
        rows.append(f"""
        <tr>
          <td><a href="/admin/events/{esc(e['id'])}">{esc(e['title'])}</a><br>
            <span class="muted small">{esc(fmt_date(e['starts_at'], with_time=False))}</span></td>
          <td>{'<span class="pill ok">Live</span>' if e['published'] else '<span class="pill warn">Draft</span>'}</td>
          <td>{s['sold']} / {s['capacity']}</td>
          <td>{s['scanned']}</td>
          <td>{money(s['revenue'], e['currency'])}</td>
        </tr>""")
    table = (f"<table><thead><tr><th>Event</th><th>Status</th><th>Sold</th>"
             f"<th>Scanned</th><th>Revenue</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
             if rows else '<p class="muted">No events yet — create your first below.</p>')
    mode = ('<span class="pill ok">Stripe test mode</span>' if live_mode
            else '<span class="pill warn">Mock payments</span>')
    return layout("Dashboard", f"""
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h1 class="mt0">Dashboard</h1>
      <a class="btn" href="/admin/events/new">+ New event</a>
    </div>
    <div class="grid cols-3 mt2">
      <div class="stat"><div class="n">{len(events)}</div><div class="l">Events</div></div>
      <div class="stat"><div class="n">{money(tot_rev)}</div><div class="l">Total revenue</div></div>
      <div class="stat"><div class="n">{mode}</div><div class="l">Payments</div></div>
    </div>
    <div class="card mt3"><div class="body">{table}</div></div>
    """, admin=True)


_NEW_EVENT_SCRIPT = """
    <script>
      let n=0;
      function addTT(name='',price='',qty=''){
        const d=document.createElement('div'); d.className='row mt2';
        d.innerHTML=`<div><input name="tt_name_${n}" placeholder="General Admission" value="${name}"></div>
          <div><input name="tt_price_${n}" type="number" min="0" step="0.01" placeholder="Price (18.00)" value="${price}"></div>
          <div><input name="tt_qty_${n}" type="number" min="1" placeholder="Qty (200)" value="${qty}"></div>`;
        document.getElementById('tts').appendChild(d); n++;
      }
      addTT('General Admission','','200');
    </script>"""


def admin_new_event(error=None):
    err = flash("err", error) if error else ""
    form = f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Create event</h1>
    {err}
    <form method="post" action="/admin/events/new">
      <div class="card"><div class="body">
        <label>Title</label>
        <input name="title" required placeholder="Friday Night Live">
        <label>Venue</label>
        <input name="venue" placeholder="The Brickyard, Manchester">
        <label>Description</label>
        <textarea name="description" placeholder="Tell people what to expect…"></textarea>
        <div class="row">
          <div><label>Date &amp; time</label>
            <input name="starts_at" type="datetime-local" required></div>
          <div><label>Currency</label>
            <select name="currency">
              <option value="GBP">GBP £</option>
              <option value="USD">USD $</option>
              <option value="EUR">EUR €</option>
            </select></div>
        </div>
        <label>Accent colour</label>
        <input name="image_url" type="color" value="#4f46e5" style="height:44px;padding:4px">
      </div></div>

      <div class="card mt2"><div class="body">
        <h2 class="mt0">Ticket types</h2>
        <p class="muted small">Add at least one. You can add more later.</p>
        <div id="tts"></div>
        <button class="btn ghost sm mt2" type="button" onclick="addTT()">+ Add ticket type</button>
      </div></div>

      <div class="mt3"><button class="btn" type="submit">Create event</button></div>
    </form>"""
    return layout("New event", form + _NEW_EVENT_SCRIPT, admin=True)


def admin_event(event, ticket_types, stats, orders, live_mode):
    tt_rows = []
    for t in ticket_types:
        tt_rows.append(f"""
        <tr><td>{esc(t['name'])}</td><td>{money(t['price'], event['currency'])}</td>
          <td>{t['sold']} / {t['quantity']}</td>
          <td><form method="post" action="/admin/ticket-types/delete" style="margin:0"
                onsubmit="return confirm('Delete this ticket type?')">
            <input type="hidden" name="id" value="{esc(t['id'])}">
            <input type="hidden" name="event_id" value="{esc(event['id'])}">
            <button class="btn ghost sm" type="submit">Remove</button></form></td></tr>""")
    order_rows = []
    for o in orders[:50]:
        order_rows.append(f"""
        <tr><td class="muted small">{esc(fmt_date(o['created_at']))}</td>
          <td>{esc(o['buyer_name'])}<br><span class="muted small">{esc(o['buyer_email'])}</span></td>
          <td>{money(o['total'], o['currency'])}</td>
          <td><span class="pill">{esc(o['provider'])}</span></td></tr>""")
    orders_tbl = (f"<table><thead><tr><th>When</th><th>Buyer</th><th>Total</th><th>Via</th></tr>"
                  f"</thead><tbody>{''.join(order_rows)}</tbody></table>"
                  if order_rows else '<p class="muted">No paid orders yet.</p>')
    pub = ("Unpublish" if event["published"] else "Publish")
    return layout(event["title"], f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <div style="display:flex;justify-content:space-between;align-items:center" class="mt2">
      <h1 class="mt0">{esc(event['title'])}</h1>
      <div>
        <a class="btn ghost sm" href="/events/{esc(event['id'])}">View public page</a>
        <form method="post" action="/admin/events/toggle" style="display:inline">
          <input type="hidden" name="id" value="{esc(event['id'])}">
          <button class="btn sm" type="submit">{pub}</button>
        </form>
      </div>
    </div>
    <div class="muted">{esc(fmt_date(event['starts_at']))} · {esc(event['venue'])}</div>
    <div class="grid cols-3 mt3">
      <div class="stat"><div class="n">{stats['sold']}</div><div class="l">Tickets sold</div></div>
      <div class="stat"><div class="n">{money(stats['revenue'], event['currency'])}</div><div class="l">Revenue</div></div>
      <div class="stat"><div class="n">{stats['scanned']}</div><div class="l">Checked in</div></div>
    </div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Ticket types</h2>
      <table><thead><tr><th>Name</th><th>Price</th><th>Sold</th><th></th></tr></thead>
      <tbody>{''.join(tt_rows) or '<tr><td colspan=4 class=muted>None yet</td></tr>'}</tbody></table>
      <form method="post" action="/admin/ticket-types/add" class="row mt3">
        <input type="hidden" name="event_id" value="{esc(event['id'])}">
        <div><input name="name" placeholder="VIP" required></div>
        <div><input name="price" type="number" min="0" step="0.01" placeholder="Price" required></div>
        <div><input name="quantity" type="number" min="1" placeholder="Qty" required></div>
        <div style="flex:0 0 auto"><button class="btn" type="submit">Add</button></div>
      </form>
    </div></div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Recent orders</h2>
      {orders_tbl}
    </div></div>
    """, admin=True)
