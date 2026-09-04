/**
 * AirMouse browser bridge — background service worker (MV3).
 *
 * Polls the active tab once per second:
 *   1. chrome.tabs.query        → the active tab
 *   2. chrome.scripting.executeScript → injects the static content.js
 *      collector (files: — never func-with-page-strings, never eval)
 *   3. chrome.tabs.sendMessage  → asks content.js for element metadata
 *   4. fetch POST               → sends the JSON state to the local
 *      AirMouse bridge server at http://127.0.0.1:17843/state
 *
 * SECURITY: data only.  The worker never evaluates page content, never
 * injects page-derived strings, and only talks to 127.0.0.1.  If the
 * bridge server is not running, every fetch fails silently.
 */

"use strict";

const BRIDGE_URL = "http://127.0.0.1:17843/state";
const POLL_MS = 1000;
const HTTP_SCHEME = /^https?:/i;

let pollTimer = null;

function tabsToState(tabs) {
  return (tabs || [])
    .filter((t) => t && t.id != null)
    .map((t) => ({
      id: String(t.id),
      title: String(t.title || ""),
      url: String(t.url || "")
    }));
}

async function collectActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id == null) return null;
  if (tab.url && !HTTP_SCHEME.test(tab.url)) return null; // skip chrome:// etc.

  // Inject the STATIC collector file (no dynamic code, no eval).
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      files: ["content.js"]
    });
  } catch (err) {
    return null; // restricted page / no host access
  }

  let resp = null;
  try {
    resp = await chrome.tabs.sendMessage(tab.id, { type: "airmouse:collect" });
  } catch (err) {
    return null;
  }
  if (!resp || !Array.isArray(resp.elements)) return null;

  let allTabs = [];
  try {
    allTabs = tabsToState(await chrome.tabs.query({}));
  } catch (err) {
    allTabs = tabsToState([tab]);
  }

  return {
    browser: "chrome", // also reports as chrome on Edge (chromium)
    tabs: allTabs,
    active_tab_id: String(tab.id),
    url: String(resp.url || tab.url || ""),
    title: String(resp.title || tab.title || ""),
    focused_element_id: String(resp.focusedElementId || ""),
    elements: resp.elements,
    timestamp: Date.now() / 1000
  };
}

function postState(state) {
  try {
    fetch(BRIDGE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state)
    }).catch(() => { /* bridge offline — ignore */ });
  } catch (err) { /* never throw from the poll loop */ }
}

function pollOnce() {
  collectActiveTab()
    .then((state) => { if (state) postState(state); })
    .catch(() => { /* ignore */ });
}

function startPolling() {
  if (pollTimer != null) return;
  pollOnce();
  pollTimer = setInterval(pollOnce, POLL_MS);
}

chrome.runtime.onInstalled.addListener(startPolling);
chrome.runtime.onStartup.addListener(startPolling);
startPolling();
