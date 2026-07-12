"""HTML rendering for Mayhem Bingo tickets. Pure string templating, stdlib only."""
import html
import json
import urllib.parse
import time

CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def _nl2br(s):
    """Escape text, then turn real line breaks into <br>.

    Browsers submit textarea newlines as CRLF, so normalise those first or a
    multi-paragraph event description renders as one run-on blob.
    """
    return esc(s).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


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
def layout(title, body, active="", admin=False, embed=False):
    nav = f'<a href="/" class="{ "active" if active=="home" else "" }">Events</a>'
    if admin:
        # Scan is an ORGANISER tool. Showing it publicly invited people to open the
        # scanner and admit their own tickets.
        nav += ('<a href="/scan">Scan</a>'
                '<a href="/admin">Dashboard</a>'
                '<a href="/admin/logout">Sign out</a>')
    else:
        nav += '<a href="/admin">Organiser</a>'

    if embed:
        # Embedded in another site (mayhembingo.co.uk): no header, footer or nav —
        # the host page provides those. Transparent background so it sits on their
        # design, and it posts its height up so the iframe can size itself.
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Mayhem Bingo</title>
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="stylesheet" href="/static/style.css">
<style>
  html,body{{background:transparent !important}}
  body{{min-height:0}}
  main{{padding:0}}
  .container{{padding:0}}
</style>
</head>
<body class="embedded">
<main><div class="container">
{body}
</div></main>
<script>
  // Tell the parent page how tall we are, so the iframe can resize to fit
  // instead of showing an inner scrollbar.
  function _postHeight() {{
    var h = document.documentElement.scrollHeight;
    try {{ parent.postMessage({{ ticketflowHeight: h }}, "*"); }} catch (e) {{}}
  }}
  window.addEventListener("load", _postHeight);
  window.addEventListener("resize", _postHeight);
  new ResizeObserver(_postHeight).observe(document.body);
</script>
</body>
</html>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Mayhem Bingo</title>
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="site"><div class="container">
  <a class="brand" href="/"><img src="/static/logo.png" alt="Mayhem Bingo" class="brandlogo"></a>
  <nav class="nav">{nav}</nav>
</div></header>
<main><div class="container">
{body}
</div></main>
<footer class="site"><div class="container">
  Mayhem Bingo · tickets · <a href="/terms">Terms &amp; conditions</a>
</div></footer>
</body>
</html>"""


def flash(kind, msg):
    return f'<div class="flash {kind}">{esc(msg)}</div>'


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
def home(events, ticket_types_by_event, live_mode, embed=False, pay_mode=None):
    # Only warn when checkout ISN'T taking real money. When live, say nothing —
    # a "test mode" notice on a real ticket page would scare buyers off.
    if pay_mode is None:
        pay_mode = "test" if live_mode else "mock"
    if pay_mode == "live":
        banner = ""
    elif pay_mode == "test":
        banner = ('<div class="banner">💳 <b>Stripe test mode</b> — cards are not '
                  'really charged. Switch to a live key before selling.</div>')
    else:
        banner = ('<div class="banner">💳 <b>Mock payment mode</b> — no Stripe key set, '
                  'so checkout is simulated and no real card is charged.</div>')
    cards = []
    for e in events:
        tts = ticket_types_by_event.get(e["id"], [])
        prices = [t["price"] for t in tts if (t["quantity"] - t["sold"]) > 0]
        price_label = ("From " + money(min(prices), e["currency"])) if prices else "Sold out"
        d, m = date_badge(e["starts_at"])
        accent = e["image_url"] if (e["image_url"] or "").startswith("#") else "#4f46e5"
        _img = (e["image"] or "") if "image" in e.keys() else ""
        # Show the WHOLE poster (contain), not a cropped middle (cover) — these are
        # adverts, and cropping would cut off the artwork's edges. Any leftover
        # space is filled with the accent colour so it still looks deliberate.
        cover_style = (
            f"background-image:url('{esc(_img)}');background-size:contain;"
            f"background-repeat:no-repeat;background-position:center;background-color:{esc(accent)}"
            if _img else f"background:{esc(accent)}"
        )
        cards.append(f"""
        <a class="card event-card" href="/events/{esc(e['id'])}"{' target="_top"' if embed else ''}>
          <div class="event-cover" style="{cover_style}">
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
        grid = f'<div class="grid events">{"".join(cards)}</div>'
    heading = "" if embed else (
        '<h1>Upcoming events</h1>'
        '<p class="lead">Find your next night out and grab tickets in seconds.</p>'
    )
    return layout("Events", f"""
    {banner}
    {heading}
    {grid}
    """, active="home", embed=embed)


def _maps_link(event):
    """The venue's full address, plainly shown.

    No directions button — people just need to know where the place is; their
    phone's maps app is a copy-paste away if they want it.
    """
    addr = (event["address"] or "").strip() if "address" in event.keys() else ""
    if not addr:
        return ""
    return f'<p class="venue-addr">{_nl2br(addr)}</p>'


def event_detail(event, ticket_types, live_mode, error=None, fee_cfg=None,
                 show_remaining=False, price_notice=None, terms_required=False):
    fee_cfg = fee_cfg or {"percent": 0, "fixed": 0, "label": "Booking fee",
                          "enabled": False}
    d = fmt_date(event["starts_at"])
    accent = event["image_url"] if (event["image_url"] or "").startswith("#") else "#4f46e5"
    _img = (event["image"] or "") if "image" in event.keys() else ""
    cover_style = (
        f"background-image:url('{esc(_img)}');background-size:contain;"
        f"background-repeat:no-repeat;background-position:center;background-color:{accent}"
        if _img else f"background:{accent}"
    )
    # A poster deserves room on the event's own page; a plain accent block doesn't.
    cover_h = "min(70vh, 520px)" if _img else "150px"
    rows = []
    any_available = False
    for t in ticket_types:
        remaining = t["quantity"] - t["sold"]
        avail = remaining > 0
        any_available = any_available or avail

        # The tier price, not the base price — and the SAME function checkout uses,
        # so what's displayed is what's charged.
        price, tier_name = (t.get("_price"), t.get("_tier")) if "_price" in t else (t["price"], None)
        tier_info = t.get("_tier_info")

        control = (f"""
          <div class="stepper" data-price="{price}">
            <button type="button" onclick="step('{t['id']}',-1)">−</button>
            <input id="q_{t['id']}" name="qty_{t['id']}" value="0" readonly>
            <button type="button" onclick="step('{t['id']}',1)" data-max="{remaining}">+</button>
          </div>
          <input type="hidden" name="quoted_{t['id']}" value="{price}">""" if avail
          else '<span class="pill bad">Sold out</span>')
        # The "N left" count is hidden by default — it tells buyers how well (or
        # badly) an event is selling. The stepper's data-max still enforces the real
        # limit, so nothing can be oversold; this only affects what's displayed.
        # A low-stock nudge is shown instead, which creates urgency without
        # advertising a quiet night.
        if show_remaining:
            stock = f" · {remaining} left"
        elif remaining <= 10:
            stock = f' · <span class="lowstock">Only {remaining} left</span>'
        else:
            stock = ""

        # Honest urgency: driven by the real tier rules, not a fake countdown.
        nudge = ""
        if tier_info and avail:
            bits = []
            if tier_info["left_in_tier"] is not None and tier_info["left_in_tier"] <= 25:
                bits.append(f"{tier_info['left_in_tier']} left at this price")
            if tier_info["until_date"]:
                bits.append("until " + time.strftime(
                    "%d %b", time.localtime(int(tier_info["until_date"]))))
            label = esc(tier_info["name"])
            nudge = (f'<div class="tiernote">{label} — {esc(" · ".join(bits))}</div>'
                     if bits else f'<div class="tiernote">{label}</div>')

        rows.append(f"""
          <div class="tt-row">
            <div>
              <h3>{esc(t['name'])}</h3>
              <div class="muted small">{money(price, event['currency'])}{stock}</div>
              {nudge}
            </div>
            <div>{control}</div>
          </div>""")
    terms_box = ("" if not terms_required else """
        <label class="termsbox mt3">
          <input type="checkbox" name="accept_terms" value="1" id="acceptTerms" required>
          <span>I have read and accept the
            <a href="/terms" target="_blank" rel="noopener">terms &amp; conditions</a>.</span>
        </label>""")

    err = flash("err", error) if error else ""
    if price_notice:
        lines = "".join(
            f"<li><b>{esc(c['name'])}</b>: {money(c['was'])} → "
            f"<b>{money(c['now'])}</b>"
            + (f" ({esc(c['tier'])})" if c.get("tier") else "") + "</li>"
            for c in price_notice)
        err = (f'<div class="flash err"><b>The price changed while you were '
               f'deciding.</b><ul style="margin:8px 0 0 18px">{lines}</ul>'
               f'<div class="mt2">Your tickets are still reserved at the new price — '
               f'just hit Checkout again to confirm.</div></div>') + err
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
        <div class="mt2">
          <label>Phone number</label>
          <input name="buyer_phone" type="tel" required placeholder="07700 900123"
                 autocomplete="tel">
          <p class="muted small mt1">So we can reach you about this booking.</p>
        </div>
        <div class="mt2">
          <label>Discount code <span class="muted small">(optional)</span></label>
          <input name="discount_code" placeholder="e.g. EARLYBIRD"
                 style="text-transform:uppercase" autocomplete="off">
        </div>
        <div class="mt3 feebox" id="feebox" style="display:none">
          <div class="feerow"><span class="muted">Tickets</span>
            <span id="sub">{money(0, event['currency'])}</span></div>
          <div class="feerow"><span class="muted">{esc(fee_cfg['label'])}</span>
            <span id="fee">{money(0, event['currency'])}</span></div>
        </div>
        {terms_box}
        <div class="mt3" style="display:flex;align-items:center;justify-content:space-between">
          <div class="muted">Total <span id="total" style="color:var(--ink);font-size:20px;font-weight:700">{money(0, event['currency'])}</span></div>
          <button class="btn" id="checkoutbtn" type="submit" disabled>Checkout →</button>
        </div>
        <p class="muted small mt1" id="feenote" style="display:none">
          {esc(fee_cfg['label'])} is charged once per booking, not per ticket.</p>
      </form>""" if any_available else '<div class="flash info">This event is sold out.</div>'

    body = f"""
    <a href="/" class="muted small">← All events</a>
    <div class="card mt2" style="overflow:hidden">
      <div class="event-cover" style="aspect-ratio:auto;height:{cover_h};{cover_style}"></div>
      <div class="body">
        <span class="pill">{esc(d)}</span>
        <h1 class="mt2">{esc(event['title'])}</h1>
        <p class="lead">{esc(event['venue'])}</p>
        {_maps_link(event)}
        <p>{_nl2br(event['description'])}</p>
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
        let sub = 0, count = 0;
        document.querySelectorAll('.stepper').forEach(s=>{{
          const price = parseInt(s.getAttribute('data-price'));
          const q = parseInt(s.querySelector('input').value||'0');
          sub += price*q; count += q;
        }});
        // Booking fee: once per booking, not per ticket. Mirrors calc_booking_fee()
        // on the server — the server's figure is authoritative, this is the preview.
        const FEE_PCT = {fee_cfg['percent']}, FEE_FIXED = {fee_cfg['fixed']};
        let fee = 0;
        if(sub > 0 && (FEE_PCT > 0 || FEE_FIXED > 0)){{
          fee = Math.round(sub * FEE_PCT / 100) + FEE_FIXED;
        }}
        const box = document.getElementById('feebox');
        const note = document.getElementById('feenote');
        if(fee > 0){{
          document.getElementById('sub').textContent = fmt(sub);
          document.getElementById('fee').textContent = fmt(fee);
          if(box) box.style.display = '';
          if(note) note.style.display = '';
        }} else {{
          if(box) box.style.display = 'none';
          if(note) note.style.display = 'none';
        }}
        document.getElementById('total').textContent = fmt(sub + fee);
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


def success(order, event, tickets, qr_svgs, emailed=False, email_on=False):
    tks = []
    for t in tickets:
        tks.append(f"""
        <div class="card mt2 ticket-print"><div class="body center">
          <img src="/static/logo.png" alt="Mayhem Bingo" class="ticketlogo">
          <div class="pill ok">Valid ticket</div>
          <h3 class="mt2">{esc(t['ticket_name'])}</h3>
          <div class="muted small">{esc(event['title'])}</div>
          <div class="muted small">{esc(fmt_date(event['starts_at']))}</div>
          <div class="muted small">{esc(event.get('venue') or '')}</div>
          {f'<div class="muted small ticket-addr">{_nl2br(event["address"])}</div>' if ("address" in event.keys() and event["address"]) else ''}
          <div class="qr qr-sm">{qr_svgs[t['code']]}</div>
          <div class="code">{esc(t['code'])}</div>
          <a class="btn ghost sm no-print" href="/t/{esc(t['code'])}">Open full ticket</a>
        </div></div>""")

    if emailed:
        mail_line = (f'<p class="muted small no-print">📧 We\'ve emailed your tickets to '
                     f'<b>{esc(order["buyer_email"])}</b>.</p>')
    elif email_on:
        mail_line = ('<p class="muted small no-print">We couldn\'t email your tickets just now — '
                     '<b>please screenshot or print this page</b>, or keep the ticket links.</p>')
    else:
        mail_line = ('<p class="muted small no-print"><b>Save these now</b> — screenshot or print '
                     'this page, or keep the ticket links. They aren\'t emailed.</p>')

    return layout("You're in!", f"""
    <div class="narrow" style="margin:0 auto">
      <div class="center">
        <div class="pill ok">Payment successful</div>
        <h1 class="mt2">You're going! 🎉</h1>
        <p class="lead">{len(tickets)} ticket{'s' if len(tickets)!=1 else ''} for
          <b>{esc(event['title'])}</b>.<br>Show the QR at the door.</p>
        {mail_line}
        <p class="no-print"><button class="btn ghost sm" onclick="window.print()">🖨️ Print tickets</button></p>
      </div>
      {''.join(tks)}
      <div class="center mt3 no-print"><a href="/" class="muted">← Back to events</a></div>
    </div>""")


def _ticket_addr(t):
    """The venue's full address on the ticket itself — so someone holding the
    ticket (on screen or printed) knows exactly where to go."""
    try:
        addr = (t["event_address"] or "").strip()
    except (KeyError, TypeError, IndexError):
        addr = ""
    if not addr:
        return ""
    return f'<div class="muted small ticket-addr">{_nl2br(addr)}</div>'


def ticket_page(t, qr_svg, wallet_on=False):
    # Only worth showing on Apple devices — a .pkpass does nothing elsewhere. We
    # detect client-side rather than sniffing the User-Agent server-side.
    wallet_btn = ("" if not wallet_on else f"""
    <div class="center mt2 no-print" id="walletWrap" style="display:none">
      <a class="applewallet" href="/t/{esc(t['code'])}/pass">
        <span class="aw-icon"></span>
        <span class="aw-text"><small>Add to</small><b>Apple Wallet</b></span>
      </a>
    </div>
    <script>
      // Show the Wallet button only on iPhone/iPad/Mac — a .pkpass is useless
      // on Android or Windows and would just download a file they can't open.
      (function(){{
        var ua = navigator.userAgent || '';
        var isApple = /iPhone|iPad|iPod|Macintosh/.test(ua);
        if(isApple){{ document.getElementById('walletWrap').style.display = ''; }}
      }})();
    </script>""")

    return layout("Ticket", f"""
    <div class="ticket ticket-print">
      <div class="top">
        <img src="/static/logo.png" alt="Mayhem Bingo" class="ticketlogo">
        <div class="pill {'ok' if t['status']=='valid' else 'warn'}">
          {'Valid' if t['status']=='valid' else 'Already used'}</div>
        <h2 class="mt2 mt0">{esc(t['event_title'])}</h2>
        <div class="muted small">{esc(fmt_date(t['event_starts_at']))}</div>
        <div class="muted small">{esc(t['event_venue'])}</div>
        {_ticket_addr(t)}
        <div class="mt2"><span class="pill">{esc(t['ticket_name'])}</span></div>
        <span class="notch l"></span><span class="notch r"></span>
      </div>
      <div class="qr">{qr_svg}</div>
      <div class="code">{esc(t['code'])}</div>
    </div>
    {wallet_btn}
    <div class="center mt3 no-print">
      <button class="btn ghost sm" onclick="window.print()">🖨️ Print ticket</button>
    </div>
    <div class="center mt2 no-print"><a href="/" class="muted">← All events</a></div>
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
      <div id="dbg" class="muted small center mt1"></div>
      <div id="out"></div>
      <div class="center mt2">
        <button class="btn ghost" id="startbtn" onclick="startScan()">Start camera</button>
      </div>
      <p class="muted small center mt2">Works on any phone. If the camera won't open,
        check the address starts with <b>https://</b> and that you've allowed camera access —
        or use the manual box below.</p>
      <div class="card mt3"><div class="body">
        <label>Or enter a code manually</label>
        <div class="row">
          <input id="manual" placeholder="TKT-XXXXXXXX">
          <button class="btn" style="flex:0 0 auto" onclick="check(document.getElementById('manual').value)">Check</button>
        </div>
      </div></div>
    </div>
    <script src="/static/qrscan.js"></script>
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
        const head = j.status==='ok' ? '✓ Admitted'
                   : j.status==='already' ? '⚠ Already used'
                   : j.status==='void'    ? '✕ REFUNDED — do not admit'
                   : '✕ Invalid ticket';
        let detail = '';
        if(j.ticket){ detail = `<div>${j.ticket.event_title}</div>
            <div class="muted small">${j.ticket.ticket_name} · ${j.ticket.buyer_name||''}</div>`;
          if(j.status==='already' && j.ticket.scanned_at)
            detail += `<div class="muted small">First scanned earlier</div>`;
        }

        // Group booking: this ticket is one of several on the same order. Offer to
        // wave the whole party through rather than scanning each phone in turn.
        let group = '';
        if(j.order && j.order.remaining > 0){
          group = `
            <div class="groupbox">
              <div class="groupline"><b>${j.order.buyer_name||'This booking'}</b> has
                ${j.order.total} tickets — ${j.order.admitted} in,
                <b>${j.order.remaining} still to come</b>.</div>
              <div class="row mt2">
                <button class="btn" onclick="admitAll('${j.order.id}')">
                  Admit all ${j.order.remaining}</button>
                <button class="btn ghost" onclick="dismissGroup()">Just this one</button>
              </div>
            </div>`;
        }

        out.innerHTML = `<div class="scan-result ${cls}"><div class="big">${head}</div>${detail}</div>${group}`;
        if(navigator.vibrate) navigator.vibrate(j.status==='ok'?80:[60,40,60]);
      }catch(e){
        out.innerHTML = `<div class="scan-result invalid"><div class="big">Network error</div></div>`;
      }
    }

    function dismissGroup(){
      const g = document.querySelector('.groupbox');
      if(g) g.remove();
    }

    async function admitAll(orderId){
      const out = document.getElementById('out');
      try{
        const r = await fetch('/api/admit-order', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({order_id: orderId})});
        const j = await r.json();
        if(j.status !== 'ok'){
          out.innerHTML = `<div class="scan-result invalid"><div class="big">Couldn't admit group</div></div>`;
          return;
        }
        out.innerHTML = `<div class="scan-result ok">
            <div class="big">✓ Party admitted</div>
            <div>${j.buyer_name||''}</div>
            <div class="muted small">${j.admitted} admitted just now · ${j.total} in the party</div>
          </div>`;
        if(navigator.vibrate) navigator.vibrate([80,40,80]);
      }catch(e){
        out.innerHTML = `<div class="scan-result invalid"><div class="big">Network error</div></div>`;
      }
    }
    async function startScan(){
      if(running) return;
      const reader = document.getElementById('reader');
      const out = document.getElementById('out');

      // getUserMedia only exists in a secure context (https:// or localhost).
      if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
        out.innerHTML =
          '<div class="scan-result invalid"><div class="big">Camera unavailable</div>'+
          '<div class="muted small">The camera needs an <b>https://</b> address. '+
          'Use the manual box below.</div></div>';
        return;
      }

      // BarcodeDetector is Chrome-only — iOS Safari and Firefox lack it — so fall
      // back to our own decoder rather than refusing to scan.
      let det = null;
      if('BarcodeDetector' in window){
        try{ det = new BarcodeDetector({formats:['qr_code']}); }catch(e){ det = null; }
      }
      const useFallback = !det;

      const video = document.createElement('video');
      video.setAttribute('playsinline','');   // iOS: don't hijack into fullscreen
      video.setAttribute('muted','');
      video.muted = true;
      reader.innerHTML=''; reader.appendChild(video);

      let stream;
      try{
        stream = await navigator.mediaDevices.getUserMedia(
          {video:{facingMode:{ideal:'environment'}}, audio:false});
      }catch(e){
        out.innerHTML =
          '<div class="scan-result invalid"><div class="big">Camera blocked</div>'+
          '<div class="muted small">Allow camera access for this site, then reload. '+
          'On iPhone: aA menu → Website Settings → Camera → Allow.</div></div>';
        return;
      }
      video.srcObject = stream;
      try { await video.play(); } catch(e) {}
      running = true;
      document.getElementById('startbtn').textContent = 'Scanning…';

      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d', {willReadFrequently:true});
      let lastHit = 0, frames = 0, decodes = 0, started = Date.now();

      const dbg = document.getElementById('dbg');
      function setDbg(msg){ if(dbg) dbg.textContent = msg; }
      setDbg(det ? 'Using built-in scanner…' : 'Using fallback scanner…');

      // iOS Safari reports videoWidth = 0 for a while after play() resolves, and
      // sometimes needs a nudge. Wait for real frames before we start decoding.
      let waited = 0;
      while (running && !video.videoWidth && waited < 5000) {
        await new Promise(r => setTimeout(r, 100));
        waited += 100;
      }
      if (running && !video.videoWidth) {
        setDbg('Camera gave no picture. Try reloading, or use the manual box.');
        return;
      }

      const loop = async () => {
        if(!running) return;
        const now = Date.now();
        try{
          if(det){
            const codes = await det.detect(video);
            if(codes.length && now - lastHit > 1500){ lastHit = now; check(codes[0].rawValue); }
          }else if(video.videoWidth){
            frames++;
            // Decode only a centre crop. It's where people hold the ticket, and
            // it's far less work than the whole frame — the full-frame decode was
            // slow enough on a phone to feel like nothing was happening.
            const vw = video.videoWidth, vh = video.videoHeight;
            const side = Math.min(vw, vh);
            const crop = Math.round(side * 0.8);
            const sx = Math.round((vw - crop) / 2), sy = Math.round((vh - crop) / 2);

            const target = 400;                 // decode resolution
            canvas.width = target; canvas.height = target;
            ctx.drawImage(video, sx, sy, crop, crop, 0, 0, target, target);
            const img = ctx.getImageData(0, 0, target, target);

            let text = null;
            try{ text = QRScan.decode(img); decodes++; }catch(e){ text = null; }

            if(text && now - lastHit > 1500){
              lastHit = now;
              setDbg('Got it!');
              check(text);
            } else if(frames % 10 === 0){
              const secs = ((now - started)/1000).toFixed(0);
              setDbg('Scanning… (' + frames + ' frames in ' + secs + 's) — '
                     + 'hold the QR steady in the middle, filling about half the box');
            }
          }
        }catch(e){
          setDbg('Scan error: ' + (e && e.message ? e.message : e));
        }
        // setTimeout, not requestAnimationFrame: iOS throttles rAF aggressively
        // and the decode is heavy enough to starve it.
        if(running) setTimeout(loop, 120);
      };
      loop();
    }
    </script>"""
    return layout("Scanner", body, active="scan", admin=True)


# ---------------------------------------------------------------------------
# Organiser dashboard
# ---------------------------------------------------------------------------
def admin_door(event, parties):
    total_tickets = sum(p["total"] for p in parties)
    total_in = sum(p["in_count"] for p in parties)
    to_come = total_tickets - total_in

    rows = []
    for p in parties:
        state = p["state"]
        badge = {
            "in": '<span class="pill ok">All in</span>',
            "partial": f'<span class="pill warn">{p["in_count"]}/{p["total"]} in</span>',
            "waiting": '<span class="pill">To come</span>',
        }[state]
        kinds = {}
        for t in p["tickets"]:
            kinds[t["ticket_name"]] = kinds.get(t["ticket_name"], 0) + 1
        kind_str = ", ".join(f"{n}× {esc(k)}" for k, n in kinds.items())

        act = ""
        if state != "in":
            remaining = p["total"] - p["in_count"]
            act = (f'<button class="btn sm act" '
                   f'onclick="doorAdmit(\'{esc(p["order_id"])}\', this)">'
                   f'Admit {remaining}</button>')

        rows.append(f"""
        <div class="party {state}" data-state="{state}" data-name="{esc((p['buyer_name'] or '').lower())}">
          <div>
            <div class="who">{esc(p['buyer_name'] or 'Unknown')} {badge}</div>
            <div class="meta">{kind_str}</div>
          </div>
          {act}
        </div>""")

    body = f"""
    <a href="/admin/events/{esc(event['id'])}" class="muted small">← {esc(event['title'])}</a>
    <h1 class="mt2">On the door</h1>
    <p class="lead">{esc(event['title'])} · {esc(fmt_date(event['starts_at']))}</p>

    <div class="grid cols-3 mt2">
      <div class="stat"><div class="n">{total_in}</div><div class="l">Checked in</div></div>
      <div class="stat"><div class="n">{to_come}</div><div class="l">Still to come</div></div>
      <div class="stat"><div class="n">{total_tickets}</div><div class="l">Tickets sold</div></div>
    </div>

    <div class="att-tabs mt3">
      <button class="on" data-f="all"     onclick="doorFilter(this,'all')">Everyone</button>
      <button          data-f="waiting" onclick="doorFilter(this,'waiting')">Still to come</button>
      <button          data-f="in"      onclick="doorFilter(this,'in')">Arrived</button>
    </div>

    <div class="row mt2" style="gap:8px;flex-wrap:wrap">
      <a class="btn sec sm" href="/admin/events/{esc(event['id'])}/sheet" target="_blank">
        🖨️ Printable door list</a>
      <a class="btn sec sm" href="/admin/events/{esc(event['id'])}/report.csv">
        ⤓ Download CSV</a>
    </div>
    <p class="muted small mt1">Print the door list before the night — it's your backup
      if the scanner or the wifi lets you down.</p>
    <input id="doorSearch" placeholder="Search a name…" oninput="doorSearchFn()" class="mt1">

    <div id="doorList" class="mt2">
      {''.join(rows) or '<p class="muted">No tickets sold yet.</p>'}
    </div>

    <p class="muted small mt3">Tip: keep the <a href="/scan">scanner</a> open on another tab
      — this list updates when you reload.</p>

    <script>
    let doorF = 'all';
    function doorFilter(btn, f){{
      doorF = f;
      document.querySelectorAll('.att-tabs button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      doorApply();
    }}
    function doorSearchFn(){{ doorApply(); }}
    function doorApply(){{
      const q = (document.getElementById('doorSearch').value || '').toLowerCase().trim();
      document.querySelectorAll('.party').forEach(el => {{
        const st = el.dataset.state;
        // "Still to come" includes partly-arrived parties — they've people outstanding.
        let okF = doorF === 'all'
          || (doorF === 'waiting' && (st === 'waiting' || st === 'partial'))
          || (doorF === 'in' && (st === 'in' || st === 'partial'));
        const okQ = !q || (el.dataset.name || '').includes(q);
        el.style.display = (okF && okQ) ? '' : 'none';
      }});
    }}
    async function doorAdmit(orderId, btn){{
      btn.disabled = true; btn.textContent = 'Admitting…';
      try{{
        const r = await fetch('/api/admit-order', {{method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{order_id: orderId}})}});
        const j = await r.json();
        if(j.status === 'ok') location.reload();
        else {{ btn.disabled = false; btn.textContent = 'Failed — retry'; }}
      }}catch(e){{ btn.disabled = false; btn.textContent = 'Failed — retry'; }}
    }}
    </script>
    """
    return layout("On the door", body, admin=True)


def admin_discounts(discounts, events, error=None, fee_cfg=None, saved=False,
                    show_remaining=False):
    fee_cfg = fee_cfg or {"percent": 0, "fixed": 0, "label": "Booking fee",
                          "enabled": False}
    rows = []
    for d in discounts:
        if d["kind"] == "percent":
            worth = f"{d['value']}% off"
        else:
            worth = f"{money(d['value'])} off"

        # Usage
        if d["max_uses"]:
            used = f"{d['used_count']} / {d['max_uses']}"
            exhausted = d["used_count"] >= d["max_uses"]
        else:
            used = f"{d['used_count']} · unlimited"
            exhausted = False

        expired = bool(d["expires_at"]) and time.time() > d["expires_at"]
        expiry = (time.strftime("%d %b %Y", time.localtime(int(d["expires_at"])))
                  if d["expires_at"] else "Never")

        if not d["active"]:
            state = '<span class="pill">Off</span>'
        elif expired:
            state = '<span class="pill warn">Expired</span>'
        elif exhausted:
            state = '<span class="pill warn">Used up</span>'
        else:
            state = '<span class="pill ok">Live</span>'

        scope = esc(d["event_title"]) if d["event_id"] else "All events"

        rows.append(f"""
        <tr>
          <td><code style="font-size:14px;font-weight:700">{esc(d['code'])}</code></td>
          <td>{worth}</td>
          <td class="muted small">{scope}</td>
          <td class="muted small">{used}</td>
          <td class="muted small">{expiry}</td>
          <td>{state}</td>
          <td style="white-space:nowrap">
            <form method="post" action="/admin/discounts/toggle" style="display:inline">
              <input type="hidden" name="id" value="{esc(d['id'])}">
              <input type="hidden" name="active" value="{'0' if d['active'] else '1'}">
              <button class="btn ghost sm" type="submit">
                {'Turn off' if d['active'] else 'Turn on'}</button>
            </form>
            <form method="post" action="/admin/discounts/delete" style="display:inline"
                  onsubmit="return confirm('Delete {esc(d['code'])}? Orders that already used it keep their discount.')">
              <input type="hidden" name="id" value="{esc(d['id'])}">
              <button class="btn ghost sm" type="submit">Delete</button>
            </form>
          </td>
        </tr>""")

    event_opts = "".join(
        f'<option value="{esc(e["id"])}">{esc(e["title"])}</option>' for e in events)

    # Worked example so you can see what the fee actually does to a real order.
    ex_sub = 1600   # a typical 2 x £8 booking
    ex_fee = int(round(ex_sub * fee_cfg["percent"] / 100.0)) + fee_cfg["fixed"]
    # Stripe UK: roughly 1.5% + 20p per transaction.
    ex_stripe = int(round((ex_sub + ex_fee) * 0.015)) + 20
    covered = ex_fee >= ex_stripe

    return layout("Discount codes", f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Booking fee &amp; discounts</h1>
    {flash("ok", "Booking fee saved.") if saved else ""}
    {flash("err", error) if error else ""}

    <div class="card mt2"><div class="body">
      <h2 class="mt0">Booking fee
        {'<span class="pill ok">On</span>' if fee_cfg["enabled"]
         else '<span class="pill">Off</span>'}</h2>
      <p class="muted small">Charged <b>once per booking</b> (not per ticket), on top of the
        ticket price, and shown separately at checkout. Set both to 0 to turn it off.</p>
      <form method="post" action="/admin/settings/fee">
        <div class="row mt2">
          <div><label>Percentage</label>
            <input name="fee_percent" value="{fee_cfg['percent']:g}" placeholder="5">
            <p class="muted small mt1">% of the order</p></div>
          <div><label>Plus fixed amount (£)</label>
            <input name="fee_fixed" value="{fee_cfg['fixed']/100:.2f}" placeholder="0.20"></div>
          <div><label>Call it</label>
            <input name="fee_label" value="{esc(fee_cfg['label'])}" placeholder="Booking fee"></div>
        </div>
        <div class="mt3"><button class="btn" type="submit">Save booking fee</button></div>
      </form>

      <div class="mt3 muted small" style="border-top:1px solid var(--line);padding-top:12px">
        <b>On a 2 &times; £8 booking ({money(ex_sub)}):</b><br>
        You'd charge {money(ex_fee)} — customer pays {money(ex_sub + ex_fee)}.<br>
        Stripe takes about {money(ex_stripe)} of that.
        {'<span style="color:#22c55e">Your fee covers it.</span>' if covered
         else '<span style="color:#f59e0b">Your fee does not cover it — you absorb '
              + money(ex_stripe - ex_fee) + '.</span>' if fee_cfg["enabled"]
         else 'With no fee, you absorb all of it.'}
      </div>
    </div></div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">What buyers see</h2>
      <form method="post" action="/admin/settings/display">
        <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
          <input type="checkbox" name="show_remaining" value="1"
                 style="width:auto" {'checked' if show_remaining else ''}>
          <span>Show how many tickets are left</span>
        </label>
        <p class="muted small mt1">Off by default — it tells people how well (or badly)
          an event is selling. When it's off, buyers still see "Only N left" once you're
          down to the last 10, which creates urgency without advertising a quiet night.</p>
        <div class="mt2"><button class="btn sec" type="submit">Save</button></div>
      </form>
    </div></div>

    <h2 class="mt3">Discount codes</h2>

    <div class="card mt2"><div class="body">
      {'<table><thead><tr><th>Code</th><th>Worth</th><th>Valid for</th><th>Used</th>'
       '<th>Expires</th><th></th><th></th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
       if rows else '<p class="muted">No discount codes yet.</p>'}
    </div></div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Create a code</h2>
      <form method="post" action="/admin/discounts/new">
        <div class="row mt2">
          <div><label>Code</label>
            <input name="code" required placeholder="EARLYBIRD"
                   style="text-transform:uppercase"></div>
          <div><label>Type</label>
            <select name="kind" id="dkind" onchange="dhint()">
              <option value="percent">Percentage off</option>
              <option value="fixed">Fixed amount off</option>
            </select></div>
          <div><label>Value</label>
            <input name="value" required placeholder="10" id="dval">
            <p class="muted small mt1" id="dhint">10 = 10% off</p></div>
        </div>
        <div class="row mt2">
          <div><label>Valid for</label>
            <select name="event_id">
              <option value="">All events</option>
              {event_opts}
            </select></div>
          <div><label>Usage limit <span class="muted small">(blank = unlimited)</span></label>
            <input name="max_uses" type="number" min="1" placeholder="20"></div>
          <div><label>Expires <span class="muted small">(blank = never)</span></label>
            <input name="expires_at" type="date"></div>
        </div>
        <div class="mt3"><button class="btn" type="submit">Create code</button></div>
      </form>
    </div></div>

    <script>
      function dhint(){{
        var k = document.getElementById('dkind').value;
        var h = document.getElementById('dhint');
        var v = document.getElementById('dval');
        if(k === 'percent'){{ h.textContent = '10 = 10% off'; v.placeholder = '10'; }}
        else {{ h.textContent = '2.50 = £2.50 off'; v.placeholder = '2.50'; }}
      }}
    </script>
    """, admin=True)


def admin_archive(events, stats_by_event):
    rows = []
    total = 0
    for e in events:
        s = stats_by_event[e["id"]]
        total += s["revenue"]
        rows.append(f"""
        <tr>
          <td><a href="/admin/events/{esc(e['id'])}">{esc(e['title'])}</a><br>
            <span class="muted small">{esc(fmt_date(e['starts_at'], with_time=False))}
              · {esc(e['venue'])}</span></td>
          <td>{s['sold']} / {s['capacity']}</td>
          <td>{s['scanned']}</td>
          <td>{money(s['revenue'], e['currency'])}</td>
          <td style="white-space:nowrap">
            <a class="btn ghost sm" href="/admin/events/{esc(e['id'])}/door">Door list</a>
            <form method="post" action="/admin/events/archive" style="display:inline;margin:0">
              <input type="hidden" name="id" value="{esc(e['id'])}">
              <input type="hidden" name="archived" value="0">
              <input type="hidden" name="back" value="/admin/archive">
              <button class="btn ghost sm" type="submit">Restore</button>
            </form>
          </td>
        </tr>""")

    return layout("Archive", f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Archive</h1>
    <p class="lead">Past events, out of the way but not gone.</p>

    <div class="card mt2"><div class="body">
      {'<table><thead><tr><th>Event</th><th>Sold</th><th>Scanned</th><th>Revenue</th>'
       '<th></th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
       if rows else '<p class="muted">Nothing archived yet.</p>'}
    </div></div>

    {f'<p class="muted small mt2">Total from archived events: <b>{money(total)}</b></p>' if rows else ''}

    <p class="muted small mt3">Archiving only hides an event. Its tickets stay valid and
      scannable, the door list still works, and the orders and revenue are all still
      counted. Restore it any time.</p>
    """, admin=True)


def terms_page(text):
    return layout("Terms & conditions", f"""
    <div class="narrow" style="margin:0 auto">
      <h1>Terms &amp; conditions</h1>
      <div class="termsbody mt2">{_nl2br(text)}</div>
      <p class="mt3"><a href="/" class="muted">← Back to events</a></p>
    </div>
    """)


def admin_terms(terms, saved=False):
    has = bool(terms["text"])
    return layout("Terms & conditions", f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Terms &amp; conditions</h1>
    {flash("ok", "Terms saved. Buyers must now accept them at checkout.") if saved else ""}

    <p class="lead">Write your terms here and buyers must tick to accept them before
      paying. Leave it empty and no tickbox is shown.</p>

    <div class="card mt2"><div class="body">
      <div class="row" style="justify-content:space-between;align-items:center">
        <h2 class="mt0">Your terms
          {'<span class="pill ok">Live</span>' if has else '<span class="pill">Not set</span>'}
        </h2>
        <span class="muted small">
          {'Version ' + str(terms['version']) if has else 'No terms published'}</span>
      </div>
      <form method="post" action="/admin/terms">
        <textarea name="terms_text" rows="18"
          placeholder="e.g.&#10;&#10;Tickets are non-refundable unless the event is cancelled.&#10;&#10;Entry is subject to the venue's conditions. We reserve the right to refuse admission.&#10;&#10;Over 18s only. ID may be required.&#10;&#10;Please arrive by 7:30pm; latecomers may not be admitted.">{esc(terms['text'])}</textarea>
        <p class="muted small mt1">Blank lines separate paragraphs. Every edit creates a
          new version — orders record the version the customer accepted, so you can always
          show what someone actually agreed to.</p>
        <div class="mt3">
          <button class="btn" type="submit">Save terms</button>
          {'<a class="btn sec" href="/terms" target="_blank" style="margin-left:8px">View public page</a>' if has else ''}
        </div>
      </form>
    </div></div>
    """, admin=True)


def admin_orders(orders, summary, status, search, msg=None, err=None):
    rows = []
    for o in orders:
        paid = o["status"] == "paid"
        refunded = o["status"] == "refunded"
        when = time.strftime("%d %b, %H:%M", time.localtime(int(o["created_at"])))
        qty = o["ticket_count"] or o["item_qty"] or 0
        badge = ('<span class="pill bad">Refunded</span>' if refunded
                 else '<span class="pill ok">Paid</span>' if paid
                 else '<span class="pill warn">Abandoned</span>')
        action = ("" if not paid else f'''
          <form method="post" action="/admin/orders/refund" style="margin:0"
                onsubmit="return confirm('Refund {esc(o["buyer_name"])} {money(o["total"], o["currency"])}?\n\nThis refunds them at Stripe AND voids their tickets.')">
            <input type="hidden" name="id" value="{esc(o['id'])}">
            <button class="btn ghost sm" type="submit">Refund</button>
          </form>''')
        phone = esc(o["buyer_phone"] or "—")
        rows.append(f"""
        <tr class="{'' if paid else 'abandoned'}">
          <td class="muted small">{when}</td>
          <td>
            <div style="font-weight:600">{esc(o['buyer_name'])}</div>
            <div class="muted small">
              <a href="mailto:{esc(o['buyer_email'])}">{esc(o['buyer_email'])}</a>
            </div>
            <div class="muted small"><a href="tel:{phone}">{phone}</a></div>
          </td>
          <td class="muted small">{esc(o['event_title'])}
            {f'<br><span class="muted small">T&amp;Cs v{o["terms_version"]} accepted</span>'
             if o.get("terms_accepted_at") else ''}</td>
          <td>{qty}</td>
          <td>{money(o['total'], o['currency'])}</td>
          <td>{badge}</td>
          <td>{action}</td>
        </tr>""")

    def tab(label, val):
        on = "on" if status == val else ""
        q = f"?status={val}" if val else ""
        return f'<a class="att-tabs-link {on}" href="/admin/orders{q}">{label}</a>'

    return layout("Orders", f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">All orders</h1>
    {flash("ok", msg) if msg else ""}
    {flash("err", err) if err else ""}
    <p class="lead">Every booking across every event, and the carts people didn't finish.</p>

    <div class="grid cols-3 mt2">
      <div class="stat"><div class="n">{summary['paid_count']}</div>
        <div class="l">Paid orders</div></div>
      <div class="stat"><div class="n">{money(summary['revenue'])}</div>
        <div class="l">Revenue</div></div>
      <div class="stat"><div class="n">{summary['abandoned_count']}</div>
        <div class="l">Abandoned carts · {money(summary['abandoned_value'])} lost</div></div>
    </div>

    <div class="att-tabs mt3">
      {tab("Everything", "")}
      {tab("Paid", "paid")}
      {tab("Abandoned carts", "pending")}
    </div>

    <form method="get" action="/admin/orders" class="row mt2" style="gap:8px">
      <input type="hidden" name="status" value="{esc(status)}">
      <input name="q" value="{esc(search)}" placeholder="Search name, email, phone or event…">
      <button class="btn sec" style="flex:0 0 auto" type="submit">Search</button>
      <a class="btn sec" style="flex:0 0 auto"
         href="/admin/orders.csv{f'?status={status}' if status else ''}">⤓ CSV</a>
    </form>

    <div class="card mt2"><div class="body">
      {'<table><thead><tr><th>When</th><th>Customer</th><th>Event</th><th>Tickets</th>'
       '<th>Total</th><th>Status</th><th></th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
       if rows else '<p class="muted">No orders yet.</p>'}
    </div></div>

    <p class="muted small mt2">An <b>abandoned cart</b> is someone who filled in their details
      and started checkout but never paid — so you have their email and phone, and can chase them.</p>
    """, admin=True)


def door_sheet(event, parties):
    """Printable door list. White paper, black ink, big tick boxes.

    Deliberately NOT the dark site theme — this is meant to be printed and used
    with a pen at the door when the scanner won't play ball.
    """
    total_tickets = sum(p["total"] for p in parties)
    rows = []
    for p in parties:
        for i, t in enumerate(p["tickets"]):
            # Only name the buyer on the first row of a party, so a group of 4
            # reads as one block of four tick boxes rather than four separate people.
            name = esc(p["buyer_name"] or "Unknown") if i == 0 else ""
            party = f'<span class="pty">party of {p["total"]}</span>' if (i == 0 and p["total"] > 1) else ""
            already = ' <span class="wasin">(scanned in)</span>' if t["status"] == "used" else ""
            rows.append(f"""
            <tr>
              <td class="tick"></td>
              <td class="nm">{name} {party}</td>
              <td class="tt">{esc(t['ticket_name'])}</td>
              <td class="cd">{esc(t['code'][-8:])}{already}</td>
            </tr>""")

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Door list — {esc(event['title'])}</title>
<style>
  /* Standalone page: printed, not browsed. Light theme regardless of the site. */
  body{{font-family:-apple-system,Helvetica,Arial,sans-serif;color:#000;background:#fff;
    margin:0;padding:22px;font-size:13px}}
  h1{{font-size:20px;margin:0 0 2px}}
  .sub{{color:#444;margin:0 0 4px}}
  .meta{{color:#444;font-size:12px;margin:0 0 14px}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;
    color:#555;border-bottom:2px solid #000;padding:6px 6px}}
  td{{padding:9px 6px;border-bottom:1px solid #ccc;vertical-align:middle}}
  .tick{{width:26px}}
  .tick::before{{content:"";display:block;width:17px;height:17px;border:2px solid #000;
    border-radius:3px}}
  .nm{{font-weight:600;font-size:14px}}
  .pty{{font-weight:400;color:#666;font-size:11px}}
  .tt{{color:#333;width:130px}}
  .cd{{font-family:ui-monospace,Menlo,monospace;color:#333;width:150px;font-size:12px}}
  .wasin{{color:#0a7a34;font-weight:600;font-family:inherit;font-size:11px}}
  tr{{break-inside:avoid;page-break-inside:avoid}}
  .noprint{{margin-bottom:16px}}
  @media print{{ .noprint{{display:none}} body{{padding:0}} @page{{margin:12mm}} }}
</style>
</head><body>
  <div class="noprint">
    <button onclick="window.print()"
      style="font-size:15px;padding:10px 18px;cursor:pointer">🖨️ Print this list</button>
    <a href="/admin/events/{esc(event['id'])}/door" style="margin-left:12px">← Back</a>
  </div>

  <h1>{esc(event['title'])}</h1>
  <p class="sub">{esc(event['venue'])} · {esc(fmt_date(event['starts_at']))}</p>
  <p class="meta">{total_tickets} ticket{'s' if total_tickets != 1 else ''} sold ·
     {len(parties)} booking{'s' if len(parties) != 1 else ''} ·
     printed {time.strftime('%d/%m/%Y %H:%M')}</p>

  <table>
    <thead><tr>
      <th></th><th>Name</th><th>Ticket</th><th>Code (last 8)</th>
    </tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="4">No tickets sold.</td></tr>'}</tbody>
  </table>
</body></html>"""


def admin_login(error=None, next_url=""):
    err = flash("err", error) if error else ""
    nxt = (f'<input type="hidden" name="next" value="{esc(next_url)}">'
           if next_url else "")
    return layout("Organiser sign in", f"""
    <div class="narrow" style="margin:40px auto 0">
      <div class="card"><div class="body">
        <h1 class="mt0">Organiser sign in</h1>
        <p class="muted">Manage your events, tickets and door check-ins.</p>
        {err}
        <form method="post" action="/admin/login">
          {nxt}
          <label>Password</label>
          <input name="password" type="password" autofocus required>
          <button class="btn full mt3" type="submit">Sign in</button>
        </form>
        <p class="muted small mt2">Default password is <code>admin123</code> —
          set <code>ADMIN_PASSWORD</code> to change it.</p>
      </div></div>
    </div>""")


def admin_dashboard(events, stats_by_event, live_mode, mail_on=False, mail_from="", mail_reply="", wallet_on=False, wallet_problem="", pay_mode="mock", past_count=0, archived_count=0):
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
          <td><form method="post" action="/admin/events/archive" style="margin:0">
            <input type="hidden" name="id" value="{esc(e['id'])}">
            <input type="hidden" name="archived" value="1">
            <input type="hidden" name="back" value="/admin">
            <button class="btn ghost sm" type="submit"
              title="Hide from the dashboard. Tickets stay valid.">Archive</button>
          </form></td>
        </tr>""")
    table = (f"<table><thead><tr><th>Event</th><th>Status</th><th>Sold</th>"
             f"<th>Scanned</th><th>Revenue</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
             if rows else '<p class="muted">No events yet — create your first below.</p>')
    # Three genuinely different states — conflating test with live is how someone
    # ends up taking real money while thinking they're testing.
    if pay_mode == "live":
        mode = '<span class="pill ok">● LIVE — real payments</span>'
    elif pay_mode == "test":
        mode = '<span class="pill warn">Stripe TEST mode — no real money</span>'
    else:
        mode = '<span class="pill warn">Mock payments — Stripe not connected</span>'
    # Offer a one-click tidy-up when past events are cluttering the list.
    tidy_prompt = ("" if not past_count else f'''
    <div class="flash info mt3" style="display:flex;justify-content:space-between;
         align-items:center;gap:12px;flex-wrap:wrap">
      <span>You have <b>{past_count}</b> event{'s' if past_count != 1 else ''} that
        {'have' if past_count != 1 else 'has'} already happened.</span>
      <form method="post" action="/admin/events/archive-past" style="margin:0">
        <button class="btn sec sm" type="submit">Archive them</button>
      </form>
    </div>''')

    mail_badge = ('<span class="pill ok">On</span>' if mail_on
                  else '<span class="pill bad">Off</span>')
    wallet_badge = ('<span class="pill ok">On</span>' if wallet_on
                    else '<span class="pill bad">Off</span>')
    if wallet_on:
        wallet_note = ('<p class="muted small">iPhone buyers see an '
                       '<b>Add to Apple Wallet</b> button on their ticket.</p>')
    else:
        wallet_note = (f'<p class="muted small">Apple Wallet passes are off, so no '
                       f'button is shown. Tickets still work by QR, link and print.<br>'
                       f'<code>{esc(wallet_problem)}</code><br>'
                       f'See WALLET-SETUP.md for how to generate the certificates.</p>')
    if mail_on:
        mail_note = (f'<p class="muted small">Buyers are emailed their tickets. '
                     f'Sent from <b>{esc(mail_from)}</b>, replies go to '
                     f'<b>{esc(mail_reply)}</b>. Send yourself a test:</p>')
    else:
        mail_note = ('<div class="flash err"><b>Tickets are NOT being emailed.</b><br>'
                     'No mail provider is configured. Set <code>RESEND_API_KEY</code> in '
                     'your Render environment (Environment tab), then redeploy. '
                     'Buyers can still see and print their tickets.</div>')
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

    {tidy_prompt}

    <div class="row mt3" style="gap:8px;flex-wrap:wrap">
      <a class="btn" href="/admin/orders">📋 All orders &amp; customers</a>
      <a class="btn sec" href="/admin/archive">🗄️ Archive{f' ({archived_count})' if archived_count else ''}</a>
      <a class="btn sec" href="/admin/orders?status=pending">🛒 Abandoned carts</a>
      <a class="btn sec" href="/admin/discounts">🏷️ Discount codes</a>
      <a class="btn sec" href="/admin/terms">📄 Terms &amp; conditions</a>
      <a class="btn sec" href="/admin/backup"
         title="Download the whole database. Keep it somewhere that isn't Render.">⤓ Backup database</a>
    </div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Apple Wallet {wallet_badge}</h2>
      {wallet_note}
    </div></div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Ticket emails {mail_badge}</h2>
      {mail_note}
      <div class="row mt2">
        <input id="testTo" type="email" placeholder="your@email.com">
        <button class="btn" style="flex:0 0 auto" onclick="testEmail()">Send test</button>
      </div>
      <div id="testOut" class="mt2"></div>
    </div></div>

    <script>
    async function testEmail(){{
      const to = document.getElementById('testTo').value.trim();
      const out = document.getElementById('testOut');
      if(!to){{ out.innerHTML = '<div class="flash err">Enter an email address.</div>'; return; }}
      out.innerHTML = '<div class="flash info">Sending…</div>';
      try{{
        const r = await fetch('/admin/test-email', {{method:'POST',
          headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
          body: 'to=' + encodeURIComponent(to)}});
        const j = await r.json();
        if(j.ok){{
          out.innerHTML = '<div class="flash ok">Sent. Check ' + to +
            ' (and the spam folder). From: ' + j.from + '</div>';
        }}else{{
          out.innerHTML = '<div class="flash err"><b>Failed.</b><br>' +
            (j.error || 'Unknown error') + '</div>';
        }}
      }}catch(e){{
        out.innerHTML = '<div class="flash err">Request failed: ' + e.message + '</div>';
      }}
    }}
    </script>
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


def admin_new_event(error=None, venues=None):
    err = flash("err", error) if error else ""
    venues = venues or []
    # Native <datalist> gives autocomplete with no JS library, and works on mobile.
    venue_options = "".join(
        f'<option value="{esc(v["venue"])}"></option>' for v in venues
    )
    # venue name -> the address last used for it, so picking one fills the address.
    venue_addr_map = json.dumps({v["venue"]: v["address"] for v in venues})
    form = f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Create event</h1>
    {err}
    <form method="post" action="/admin/events/new" enctype="multipart/form-data">
      <div class="card"><div class="body">
        <label>Title</label>
        <input name="title" required placeholder="Friday Night Live">
        <label>Venue</label>
        <input name="venue" id="venueInput" list="venueList" autocomplete="off"
               placeholder="The Social Club">
        <datalist id="venueList">{venue_options}</datalist>
        <label>Venue address <span class="muted small">(shown on the ticket)</span></label>
        <textarea name="address" id="addressInput" rows="3"
                  placeholder="12 High Street&#10;Huddersfield&#10;HD1 2AB"></textarea>
        <p class="muted small mt1">Pick a venue you've used before and its address fills in
          automatically. Type it once; it's remembered.</p>
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
        <label>Event image <span class="muted small">(optional — a poster or advert)</span></label>
        <input name="image_file" type="file" accept="image/*">
        <p class="muted small mt1">Portrait works best — around <b>800&times;1200</b> (2:3), like a poster.
          Max 8MB; anything bigger than 1400px is shrunk automatically so pages stay fast on a phone.</p>
        <label class="mt2">…or paste an image URL</label>
        <input name="image" type="url" placeholder="https://…  (leave blank if uploading a file)">
        <label class="mt2">Accent colour <span class="muted small">(used if there's no image)</span></label>
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
    venue_script = f"""
    <script>
      // Picking a venue you've used before fills in its address — so a repeat
      // venue is never retyped (and never mistyped).
      (function(){{
        var addrs = {venue_addr_map};
        var v = document.getElementById('venueInput');
        var a = document.getElementById('addressInput');
        if(!v || !a) return;
        function fill(){{
          var known = addrs[v.value];
          // Only auto-fill if the address box is empty or still holds the address
          // of a different known venue — never clobber something typed by hand.
          var typed = a.value.trim();
          var isKnownAddr = Object.keys(addrs).some(function(k){{
            return addrs[k].trim() === typed && typed !== '';
          }});
          if(known && (typed === '' || isKnownAddr)) a.value = known;
        }}
        v.addEventListener('change', fill);
        v.addEventListener('input', fill);
      }})();
    </script>"""
    return layout("New event", form + _NEW_EVENT_SCRIPT + venue_script, admin=True)


def _dtlocal(ts):
    """Unix seconds → the YYYY-MM-DDTHH:MM a datetime-local input expects."""
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M", _t.localtime(int(ts)))


def _cover_preview(event):
    """Show the current cover image (with a remove tickbox) if one is set."""
    img = (event["image"] or "") if "image" in event.keys() else ""
    if not img:
        return '<p class="muted small">No image set — the accent colour is used instead.</p>'
    return (
        f'<div style="margin:6px 0 10px">'
        f'<img src="{esc(img)}" alt="" style="max-width:220px;border-radius:10px;display:block">'
        f'<label class="field-inline mt1" style="font-size:13px">'
        f'<input type="checkbox" name="remove_image" value="1" style="width:auto"> '
        f'Remove this image</label></div>'
    )


def admin_event(event, ticket_types, stats, orders, live_mode, error=None, venues=None,
                tiers_by_tt=None, avail_tiers=True):
    venues = venues or []
    tiers_by_tt = tiers_by_tt or {}
    e_venue_options = "".join(
        f'<option value="{esc(v["venue"])}"></option>' for v in venues)
    e_venue_addrs = json.dumps({v["venue"]: v["address"] for v in venues})
    tt_rows = []
    for t in ticket_types:
        tiers = tiers_by_tt.get(t["id"], []) if tiers_by_tt else []
        cur_price = t.get("_price", t["price"])
        cur_tier = t.get("_tier")

        price_cell = money(cur_price, event["currency"])
        if cur_tier:
            price_cell += f'<br><span class="muted small">{esc(cur_tier)} (base {money(t["price"], event["currency"])})</span>'

        tt_rows.append(f"""
        <tr><td>{esc(t['name'])}</td><td>{price_cell}</td>
          <td>{t['sold']} / {t['quantity']}</td>
          <td><form method="post" action="/admin/ticket-types/delete" style="margin:0"
                onsubmit="return confirm('Delete this ticket type?')">
            <input type="hidden" name="id" value="{esc(t['id'])}">
            <input type="hidden" name="event_id" value="{esc(event['id'])}">
            <button class="btn ghost sm" type="submit">Remove</button></form></td></tr>""")

        # Tier rows sit under their ticket type.
        for tr in tiers:
            rule = []
            if tr["until_date"]:
                rule.append("until " + time.strftime("%d %b %Y",
                            time.localtime(int(tr["until_date"]))))
            if tr["max_qty"] is not None:
                rule.append(f"first {tr['max_qty']} sold")
            active = (cur_tier == tr["name"])
            tt_rows.append(f"""
            <tr class="tierrow">
              <td class="muted small" style="padding-left:24px">
                ↳ {esc(tr['name'])}
                {'<span class="pill ok" style="margin-left:6px">Active</span>' if active else ''}</td>
              <td class="muted small">{money(tr['price'], event['currency'])}</td>
              <td class="muted small">{esc(' · '.join(rule)) or '—'}</td>
              <td><form method="post" action="/admin/tiers/delete" style="margin:0">
                <input type="hidden" name="id" value="{esc(tr['id'])}">
                <input type="hidden" name="event_id" value="{esc(event['id'])}">
                <button class="btn ghost sm" type="submit">×</button></form></td>
            </tr>""")

        if avail_tiers:
            tt_rows.append(f"""
            <tr class="tierrow">
              <td colspan="4" style="padding-left:24px">
                <form method="post" action="/admin/tiers/add" class="row"
                      style="gap:6px;align-items:center;margin:0">
                  <input type="hidden" name="ticket_type_id" value="{esc(t['id'])}">
                  <div><input name="name" placeholder="Early bird" required></div>
                  <div><input name="price" type="number" min="0" step="0.01"
                              placeholder="Price" required></div>
                  <div><input name="until_date" type="date" title="Valid until this date"></div>
                  <div><input name="max_qty" type="number" min="1"
                              placeholder="or first N" title="Valid for the first N sold"></div>
                  <div style="flex:0 0 auto">
                    <button class="btn ghost sm" type="submit">+ Tier</button></div>
                </form>
              </td>
            </tr>""")
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
        <a class="btn sm" href="/admin/events/{esc(event['id'])}/door">🚪 On the door</a>
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

    {flash("err", error) if error else ""}

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Event details</h2>
      <form method="post" action="/admin/events/edit" enctype="multipart/form-data">
        <input type="hidden" name="id" value="{esc(event['id'])}">
        <label>Title</label>
        <input name="title" value="{esc(event['title'])}" required>
        <div class="row">
          <div><label>Venue</label>
            <input name="venue" id="eVenueInput" list="eVenueList" autocomplete="off"
                   value="{esc(event['venue'])}"></div>
          <div><label>Venue address</label>
            <textarea name="address" id="eAddressInput" rows="3"
                      placeholder="12 High Street&#10;Huddersfield&#10;HD1 2AB">{esc(event['address'] if 'address' in event.keys() else '')}</textarea></div>
          <div><label>Date &amp; time</label>
            <input name="starts_at" type="datetime-local" value="{_dtlocal(event['starts_at'])}"></div>
        </div>
        <label>Description</label>
        <textarea name="description" placeholder="Tell people what to expect…">{esc(event['description'])}</textarea>

        <label class="mt2">Event image</label>
        {_cover_preview(event)}
        <input name="image_file" type="file" accept="image/*">
        <label class="mt2">…or paste an image URL</label>
        <input name="image" type="url" placeholder="https://…">
        <label class="mt2">Accent colour <span class="muted small">(used if there's no image)</span></label>
        <input name="image_url" type="color" value="{esc(event['image_url'] if (event['image_url'] or '').startswith('#') else '#4f46e5')}" style="height:44px;padding:4px">

        <div class="mt3"><button class="btn" type="submit">Save changes</button></div>
      </form>
      <datalist id="eVenueList">{e_venue_options}</datalist>
      <script>
        (function(){{
          var addrs = {e_venue_addrs};
          var v = document.getElementById('eVenueInput');
          var a = document.getElementById('eAddressInput');
          if(!v || !a) return;
          function fill(){{
            var known = addrs[v.value];
            var typed = a.value.trim();
            var isKnownAddr = Object.keys(addrs).some(function(k){{
              return addrs[k].trim() === typed && typed !== '';
            }});
            if(known && (typed === '' || isKnownAddr)) a.value = known;
          }}
          v.addEventListener('change', fill);
          v.addEventListener('input', fill);
        }})();
      </script>
    </div></div>

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Ticket types &amp; pricing</h2>
      <p class="muted small">Add a <b>tier</b> to change the price by date ("early bird
        until 1 Aug") or by quantity ("first 50 at £6"). Tiers apply in order — the
        first one still valid wins. No tiers = the base price always applies.</p>
      <table><thead><tr><th>Name</th><th>Price</th><th>Sold / Rule</th><th></th></tr></thead>
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
