"""HTML rendering for Mayhem Bingo tickets. Pure string templating, stdlib only."""
import html
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
    nav = (
        f'<a href="/" class="{ "active" if active=="home" else "" }">Events</a>'
        f'<a href="/scan">Scan</a>'
    )
    if admin:
        nav += '<a href="/admin">Dashboard</a><a href="/admin/logout">Sign out</a>'
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
  Mayhem Bingo · tickets
</div></footer>
</body>
</html>"""


def flash(kind, msg):
    return f'<div class="flash {kind}">{esc(msg)}</div>'


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
def home(events, ticket_types_by_event, live_mode, embed=False):
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


def event_detail(event, ticket_types, live_mode, error=None):
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
      <div class="event-cover" style="aspect-ratio:auto;height:{cover_h};{cover_style}"></div>
      <div class="body">
        <span class="pill">{esc(d)}</span>
        <h1 class="mt2">{esc(event['title'])}</h1>
        <p class="lead">{esc(event['venue'])}</p>
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
        const head = j.status==='ok'?'✓ Admitted':(j.status==='already'?'⚠ Already used':'✕ Invalid ticket');
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
    return layout("Scanner", body, active="scan")


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


def admin_dashboard(events, stats_by_event, live_mode, mail_on=False, mail_from="", mail_reply="", wallet_on=False, wallet_problem=""):
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


def admin_new_event(error=None):
    err = flash("err", error) if error else ""
    form = f"""
    <a href="/admin" class="muted small">← Dashboard</a>
    <h1 class="mt2">Create event</h1>
    {err}
    <form method="post" action="/admin/events/new" enctype="multipart/form-data">
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
        <label>Event image <span class="muted small">(optional — a poster or photo)</span></label>
        <input name="image_file" type="file" accept="image/*">
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
    return layout("New event", form + _NEW_EVENT_SCRIPT, admin=True)


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

    <div class="card mt3"><div class="body">
      <h2 class="mt0">Event details</h2>
      <form method="post" action="/admin/events/edit" enctype="multipart/form-data">
        <input type="hidden" name="id" value="{esc(event['id'])}">
        <label>Title</label>
        <input name="title" value="{esc(event['title'])}" required>
        <div class="row">
          <div><label>Venue</label>
            <input name="venue" value="{esc(event['venue'])}"></div>
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
    </div></div>

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
