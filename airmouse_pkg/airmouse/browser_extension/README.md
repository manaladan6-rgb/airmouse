# AirMouse Browser Bridge — Chrome/Edge Extension (MV3)

This folder ships the source of the minimal Manifest V3 extension that
feeds page metadata to the AirMouse v10 browser-control system
(mission §11).

## What it does

1. `background.js` wakes up every **1 second** and:
   - finds the active tab (`chrome.tabs.query`),
   - injects the **static** collector file `content.js`
     (`chrome.scripting.executeScript` — never dynamic code, never eval),
   - asks it for element metadata
     (`chrome.tabs.sendMessage` → `airmouse:collect`),
   - `POST`s the assembled JSON state to the local bridge server:
     `http://127.0.0.1:17843/state`.
2. `content.js` collects, for each interactive element
   (buttons, links, inputs, tabs, headings):
   - `role` (button / link / input / tab / heading / text),
   - visible `text` (trimmed to 120 chars),
   - `tag`,
   - `bbox` via `getBoundingClientRect()` **normalized to the viewport**
     (0..1 on all four values),
   - `value` (inputs only; password fields are masked to empty),
   - `href` (links only).

   It also reports which element currently has focus and the page
   title/URL.

The Python side that consumes this stream lives in
`airmouse/browser_bridge.py` (`BrowserBridgeServer`) and
`airmouse/browser.py` (`BrowserTargetMapper`, `SemanticBrowserResolver`,
`BrowserController`).

## Loading it (unpacked)

1. Start the AirMouse bridge server (default port **17843**), e.g. the
   AirMouse app with `--browser --browser-bridge`, or:
   `python3 -c "from airmouse.browser_bridge import BrowserBridgeServer; s = BrowserBridgeServer(); s.start(); input()"`.
2. Open `chrome://extensions` (Edge: `edge://extensions`).
3. Enable **Developer mode** (toggle, top-right).
4. Click **Load unpacked** and select **this folder**
   (`browser_extension/`) — the one containing `manifest.json`.
5. Browse normally. Every second, the active tab's metadata is POSTed to
   `http://127.0.0.1:17843/state`. You can verify with:
   `curl http://127.0.0.1:17843/health` and
   `curl http://127.0.0.1:17843/state`.

If you change the bridge server port, update `BRIDGE_URL` at the top of
`background.js` to match.

## Security model

- **Localhost only.** The extension only ever talks to
  `127.0.0.1:17843`. The Python server binds to `127.0.0.1` and is never
  exposed to the network.
- **Data only.** The extension collects *metadata* (roles, text, boxes).
  It contains no `eval`, no `new Function`, and never injects
  page-derived strings as code. The Python resolver matches utterances
  against its own fixed grammar — page text can never become a command.
- **Password fields are never reported** (value masked to empty).
- Only the most recent state is stored server-side, capped at 256 KB.

## Verification status — hardware-unverified

The extension source in this folder is shipped as reference implementation
and has been statically reviewed, but **running it inside a real Chrome or
Edge browser is hardware-unverified** in this headless build environment
(no display, no browser automation). The Python side of the bridge is
fully covered by deterministic tests (`tests/test_browser.py`,
`verify_bridge_server()` round-trip). If the extension misbehaves in your
browser, check `chrome://extensions` → the card's **Errors** button and
the service-worker console (click *service worker* on the card).
