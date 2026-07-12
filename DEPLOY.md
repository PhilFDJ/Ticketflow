# Deploying TicketFlow (Mayhem Bingo)

## What changed from the original
Three production fixes — the app is otherwise untouched:

1. **HTTPS URLs behind a proxy.** `base_url()` hardcoded `http://`. Render
   terminates TLS, so Stripe would have been handed `http://` return URLs and
   **rejected them in live mode**. It now reads `X-Forwarded-Proto`.
2. **Iframe breakout for Stripe.** Stripe's hosted checkout refuses to load in an
   iframe. Since the public site is embedded in mayhembingo.co.uk, checkout now
   navigates the *top-level* window. (Mock mode still redirects normally.)
3. **`NO_SEED=1`.** Stops the fake demo events (jazz brunch, beer festival)
   appearing on a real ticket site.

## Deploy on Render
1. Push this folder to its own GitHub repo (e.g. `PhilFDJ/ticketflow`).
2. Render → **New +** → **Blueprint** → pick the repo. It reads `render.yaml`.
3. Set these in the Render dashboard (they're marked `sync: false`):
   - `ADMIN_PASSWORD` — your organiser password. **Change it from `admin123`.**
   - `STRIPE_SECRET_KEY` — leave empty for mock; `sk_test_...` to test;
     `sk_live_...` to take real money.
4. Custom domain: add `tickets.mayhembingo.co.uk`, pointing at this service.
   Render issues the TLS certificate automatically.

**The persistent disk is not optional.** Render wipes the normal filesystem on
every deploy. TicketFlow keeps every event, order and ticket in one SQLite file,
so without the disk (mounted at `/var/data`) you would lose every ticket sold the
next time you deployed. The blueprint sets this up.

## Embedding in mayhembingo.co.uk
Embed **only the public pages**:

```html
<iframe src="https://tickets.mayhembingo.co.uk/"
        style="width:100%;height:1200px;border:0"
        title="Buy tickets"></iframe>
```

**Do NOT iframe `/scan` or `/admin`:**
- `/scan` needs the camera, which browsers block inside a cross-origin iframe
  (especially Safari/iOS). At the door, open
  `https://tickets.mayhembingo.co.uk/scan` **directly** on your phone — add it to
  your home screen. There's also a manual code-entry box as a fallback.
- `/admin` is your dashboard; keep it out of the public page. Bookmark it.

## Going live with real payments
Test first with `sk_test_...` and Stripe's test card `4242 4242 4242 4242`
(any future expiry, any CVC). Buy a ticket, scan it, check it admits once and
then reports "already used". Only then switch to `sk_live_...`.

## Known gap (worth knowing before a busy door)
Tickets are shown on screen after purchase and re-openable at `/t/<code>`, but
**there is no email delivery yet** — buyers must keep the link/screenshot. If
someone turns up having lost it, use the manual-entry box or look them up in
`/admin`. Email delivery is the obvious next addition.
