# SnappyMake IMAP Verifier (browser extension)

A tiny, **standalone** MV3 extension that shows the **latest Snapchat verification
link** and **OTP code** for the Gmail tied to the currently-open AdsPower profile.

Browsers cannot speak IMAP directly (no raw TCP sockets), so the extension asks
a backend over HTTP. It works against **either** backend:

- **The companion** (recommended, default `http://127.0.0.1:8799`) — a tiny
  self-contained Python service in [`companion/`](companion/) that runs
  independently of the main dashboard. See **Companion service** below.
- **The SnappyMake dashboard** (`http://127.0.0.1:8787`) if it happens to be
  running — it exposes the same `/api/ext/*` endpoints.

Switch between them any time via the ⚙ settings panel in the popup.

## Companion service

The companion lives in [`companion/`](companion/), uses **only the Python
standard library** (no `pip install`, no dependency on the `app/` package), and
exposes the two endpoints the popup needs.

Start it:

```bash
cd extension/companion
./run.sh
# → SnappyMake IMAP companion listening on http://127.0.0.1:8799
```

Optional env overrides:

```bash
COMPANION_PORT=8799 \
ADSPOWER_BASE=http://local.adspower.net:50325 \
ADSPOWER_API_KEY=<key-if-your-local-api-needs-one> \
IMAP_HOST=imap.gmail.com IMAP_PORT=993 \
./run.sh
```

How it gets credentials:

- `GET /api/ext/context` calls the **AdsPower Local API** (auto-detects
  `http://local.adspower.net:50325` / `http://127.0.0.1:50325`), lists profiles
  whose browser is **open**, and parses `Email: <addr>` (and `Pass: <app_pw>`)
  out of each profile's remark. Passwords are **never** returned here.
- `GET /api/ext/latest?account=<email>` resolves the app password from the
  remark (or from an explicit `&password=<app_pw>` for manual use), connects to
  Gmail over IMAP SSL, scans INBOX + All Mail + Spam, and returns the newest
  verification link + OTP.

> ⚠️ **Security caveat:** to make this work without OAuth, SnappyMake stores the
> Gmail **app password in plaintext inside each AdsPower profile's remark**
> (`… · Email: x@gmail.com · Pass: abcd…`). Anyone with access to your AdsPower
> profiles can read it. Use throwaway Gmail accounts + app passwords, and revoke
> them when done. If you don't want passwords in the remark, leave `Pass:` out
> and type the app password manually in the popup instead.

## What it does

- **Auto mode:** resolves the account from the **currently-open** AdsPower
  profile only (`/api/ext/context`), parsing its `Email: <address>` remark, and
  fetches the latest verification **link** and **OTP** (`/api/ext/latest`).
  There is **no dropdown of all profiles** — if several are open it uses the
  most recent and shows a small `1 of N open` note.
- **Manual mode:** when **no** profile is open, the popup shows an email +
  app-password field and a **Fetch** button, so you can look up **any** mailbox
  (not just pool ones) via `/api/ext/latest?account=&password=`.
- The status line states the active mode: `Auto: <email> (open profile)` or
  `Manual entry`.
- **Saved defaults:** the ⚙ settings panel keeps a default email + app password
  (persisted via `chrome.storage.local`) that pre-fill the manual fields.
- Two sections (link / OTP) with copy / open actions. **No background
  polling** — press **Refresh** to re-check.

## Files

| File | Purpose |
| --- | --- |
| `manifest.json` | MV3 manifest (kept at the archive root for AdsPower) |
| `popup.html` / `popup.css` / `popup.js` | The popup UI + logic |
| `icons/` | Toolbar icons (16/48/128) |
| `package.sh` | Builds `dist/snappymake-imap-extension.zip` |

## Build the ZIP

```bash
cd extension
./package.sh
```

This produces `extension/dist/snappymake-imap-extension.zip` with
`manifest.json` at the **top level** of the archive (required by AdsPower).

## Load into AdsPower

AdsPower → **Extensions / 扩展程序** (or a profile's *Advanced → Extensions*):

**Option A — Local extension (unpacked folder)**
1. Choose *Local extension* and point it at the unpacked folder:
   `.../snappymake/extension`
   (the folder that directly contains `manifest.json`).
2. Attach it to the profile(s) you want and open the browser.

**Option B — ZIP upload**
1. Build the zip with `./package.sh`.
2. Upload `extension/dist/snappymake-imap-extension.zip`.
   AdsPower extracts it and expects `manifest.json` at the root — which this
   zip provides.

> Note: adding an extension changes the browser fingerprint slightly. Load it
> only on the operator/utility profiles where you actually need the popup, not
> on the clean signup profiles you want to keep blank.

## Load into plain Chrome (for testing)

`chrome://extensions` → enable **Developer mode** → **Load unpacked** →
select the `extension/` folder.

## Configure the backend URL

The default is the companion at `http://127.0.0.1:8799`. To point at the main
dashboard instead (`http://127.0.0.1:8787`) or a custom port/host on the same
machine, click the ⚙ button in the popup, enter the base URL, and press
**Save**. The value is stored per-browser via `chrome.storage.local`.

The ⚙ panel also holds **manual credentials** — a Gmail address and an
app-password field with **Save credentials** / **Clear**. When set, these are
remembered across popup opens and used as the fallback whenever no AdsPower
profile account resolves (e.g. AdsPower is closed or the remark has no
`Email:`). The password field is masked and never written to the console.

The extension only requests `http://127.0.0.1/*` and `http://localhost/*` host
permissions (all ports), so the backend must be reachable on the same machine.

## Backend endpoints used

- `GET /api/ext/context` → `{ ok, accounts: [{ profile_id, name, email }] }`
  (open profiles + email parsed from the profile remark).
- `GET /api/ext/latest?account=<email>[&password=<app_pw>]` →
  `{ ok, email, link, otp, subject, from, ts, folder }`.

Served by **either** the companion (`companion/companion.py`) or the SnappyMake
FastAPI app, both CORS-enabled for `chrome-extension://` origins. The zip built
by `package.sh` contains only the extension assets — the companion is a
host-side service you run separately (see **Companion service** above).
