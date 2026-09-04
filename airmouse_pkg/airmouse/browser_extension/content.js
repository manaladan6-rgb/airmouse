/**
 * AirMouse browser bridge — content collector (MV3).
 *
 * Collects interactive-element METADATA ONLY: role, visible text, tag,
 * bounding box (normalized to the viewport), value (masked for password
 * inputs) and href.  The result is a plain JSON-serializable object that
 * is sent to the background service worker; NOTHING from the page is
 * ever evaluated or executed here — this file contains no eval, no
 * Function(), no injected page strings.
 *
 * Injected on demand by background.js via chrome.scripting.executeScript
 * every poll cycle.  The listener registration is idempotent.
 */
(() => {
  "use strict";

  const MAX_ELEMENTS = 200;
  const SELECTOR = [
    "a[href]", "button", "input", "select", "textarea",
    "[role='button']", "[role='link']", "[role='tab']", "[role='heading']",
    "h1", "h2", "h3"
  ].join(",");

  function round4(n) {
    const v = Number(n);
    return isFinite(v) ? Math.round(v * 10000) / 10000 : 0;
  }

  function roleOf(el, tag) {
    const aria = (el.getAttribute && el.getAttribute("role") || "").toLowerCase();
    if (aria === "button" || aria === "link" || aria === "tab" ||
        aria === "heading") {
      return aria;
    }
    if (tag === "button") return "button";
    if (tag === "a") return "link";
    if (tag === "input" || tag === "textarea" || tag === "select") {
      return "input";
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    return "text";
  }

  function collect() {
    const vw = window.innerWidth || document.documentElement.clientWidth || 1;
    const vh = window.innerHeight || document.documentElement.clientHeight || 1;
    const nodes = Array.from(document.querySelectorAll(SELECTOR))
      .slice(0, MAX_ELEMENTS);

    const elements = nodes.map((el, i) => {
      const r = el.getBoundingClientRect();
      const tag = (el.tagName || "").toLowerCase();
      const role = roleOf(el, tag);
      const isPassword = tag === "input" && (el.type || "") === "password";
      const interactive = tag === "a" || tag === "button" || tag === "input" ||
        tag === "select" || tag === "textarea" ||
        !!el.getAttribute("role");
      let href = "";
      if (el.getAttribute) {
        href = String(el.getAttribute("href") || "").slice(0, 300);
      }
      return {
        id: "ae-" + i,
        role: role,
        text: String(el.innerText || el.value ||
          (el.getAttribute && el.getAttribute("aria-label")) || "")
          .trim().slice(0, 120),
        tag: tag,
        bbox: [
          Math.max(0, round4(r.left / vw)),
          Math.max(0, round4(r.top / vh)),
          Math.max(0, round4(r.width / vw)),
          Math.max(0, round4(r.height / vh))
        ],
        actionable: interactive,
        value: isPassword ? "" : String(el.value || "").slice(0, 120),
        href: href,
        confidence: 1.0,
        untrusted: true
      };
    });

    let focusedElementId = "";
    const active = document.activeElement;
    if (active) {
      const idx = nodes.indexOf(active);
      if (idx >= 0) focusedElementId = "ae-" + idx;
    }

    return {
      elements: elements,
      focusedElementId: focusedElementId,
      title: String(document.title || ""),
      url: String(location.href || "")
    };
  }

  if (!window.__airmouseBridgeLoaded) {
    window.__airmouseBridgeLoaded = true;
    try {
      chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
        if (msg && msg.type === "airmouse:collect") {
          try {
            sendResponse(collect());
          } catch (err) {
            sendResponse({ elements: [], focusedElementId: "",
              title: "", url: "" });
          }
        }
        return undefined; // synchronous response only
      });
    } catch (err) {
      /* extension context invalidated — ignore this cycle */
    }
  }

  // Also answer immediately when freshly injected (background falls back
  // to a direct sendMessage right after executeScript).
  return collect();
})();
