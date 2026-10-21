// Watermark for systems marked sensitive in the desktop catalog. The list comes
// from Chrome policy (managed storage); the page and the user cannot change it.
// The overlay never takes clicks, sits above everything, and puts itself back if
// a page script removes it.
"use strict";
(async () => {
  const cfg = await chrome.storage.managed.get(["sensitive", "label"]).catch(() => ({}));
  const here = new URL(location.href);
  const hit = (cfg.sensitive || []).some(u => {
    try {
      const s = new URL(u);
      const base = s.pathname.endsWith("/") ? s.pathname : s.pathname + "/";
      return s.origin === here.origin && (s.pathname === "/" || here.pathname === s.pathname || here.pathname.startsWith(base));
    } catch (e) { return false; }
  });
  if (!hit) return;

  const who = await chrome.runtime.sendMessage({ q: "whoami" }).catch(() => ({ user: "unknown user" }));
  const host = document.createElement("cc-watermark");
  const root = host.attachShadow({ mode: "closed" });
  const layer = document.createElement("div");
  root.appendChild(layer);
  host.style.cssText = "all:initial !important; position:fixed !important; inset:0 !important; z-index:2147483647 !important;" +
    "pointer-events:none !important; display:block !important;";

  function esc(s) { return String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`); }
  function paint() {
    const stamp = new Date().toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    const line1 = [who.user, who.host].filter(Boolean).join(" · ");
    const line2 = [stamp, cfg.label].filter(Boolean).join(" · ");
    // Grey text with a light outline reads on dark and light pages and survives a phone photo.
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200">
      <g transform="rotate(-24 180 100)" font-family="Arial, sans-serif" font-size="17" font-weight="700" text-anchor="middle"
         fill="rgba(105,105,105,.36)" stroke="rgba(255,255,255,.22)" stroke-width=".6">
        <text x="180" y="92">${esc(line1)}</text><text x="180" y="116" font-size="14">${esc(line2)}</text></g></svg>`;
    layer.style.cssText = "position:absolute; inset:0; background-repeat:repeat;" +
      `background-image:url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}");`;
    host.dataset.user = who.user || "";        // read by tests
  }
  function attach() {
    if (!host.isConnected) (document.documentElement || document).appendChild(host);
  }
  paint();
  attach();
  new MutationObserver(attach).observe(document, { childList: true, subtree: true });
  setInterval(() => { paint(); attach(); }, 30000);
})();
