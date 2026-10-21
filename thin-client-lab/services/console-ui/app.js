// Call Center Console - single-page app talking to cc-console's /api.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const main = $("#main");
let refreshTimer = null;
let liveToken = 0;

// ---- helpers -------------------------------------------------------------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function time(ts) { return new Date(ts * 1000).toLocaleTimeString(); }
function dateTime(ts) { return new Date(ts * 1000).toLocaleString(); }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), 3500);
}
const ROLE = { session_host: "Session host", broker: "Broker", endpoint: "Endpoint", laptop: "Agent laptop" };
const STATE = {
  compliant: ["b-ok", "Compliant"], warning: ["b-warn", "Warnings"],
  noncompliant: ["b-bad", "Not compliant"], unknown: ["b-info", "Unknown"],
};
function stateBadge(s) { const [c, t] = STATE[s] || STATE.unknown; return `<span class="badge ${c}">${t}</span>`; }
function onlineDot(d) { return `<span class="dot ${d.online ? "on" : "off"}"></span>${d.online ? "Online" : "Offline"}`; }
function pct(v) { return v == null ? "–" : `${Math.round(v)}%`; }
function bar(v) { return `<div class="bar" title="${pct(v)}"><span style="width:${Math.min(100, v || 0)}%"></span></div>`; }
function spark(hist, idx = 1) {
  const pts = (hist || []).map(h => h[idx]).filter(v => v != null);
  if (pts.length < 2) return `<div class="muted">collecting…</div>`;
  const w = 300, h = 44, step = w / (pts.length - 1);
  const line = pts.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (Math.min(v, 100) / 100) * (h - 4)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${line}"/></svg>`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", "X-CC-Console": "1" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (r.status === 401) { showLogin(); throw new Error("signed out"); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error(data?.error || `HTTP ${r.status}`);
  return data;
}

// ---- sign-in --------------------------------------------------------------
function showLogin() {
  clearInterval(refreshTimer);
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#login-error").textContent = "";
  try {
    const r = await api("/api/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    e.target.reset();
    start(r.user);
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); });

function start(user) {
  $("#me").textContent = user;
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  route();
}

// ---- routing ----------------------------------------------------------------
const pages = { overview, devices, device, sessions, apps, activity };
function route() {
  clearInterval(refreshTimer);
  const [, page = "overview", arg] = location.hash.split("/");
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page ||
    (page === "device" && a.dataset.page === "devices")));
  const fn = pages[page] || overview;
  const run = () => fn(arg ? decodeURIComponent(arg) : undefined).catch(e => {
    if (e.message !== "signed out") main.innerHTML = `<div class="card error">${esc(e.message)}</div>`;
  });
  run();
  if (["overview", "devices", "device", "sessions"].includes(page)) refreshTimer = setInterval(run, 5000);
}
window.addEventListener("hashchange", route);

// ---- overview -----------------------------------------------------------------
async function overview() {
  const s = await api("/api/summary");
  main.innerHTML = `
    <h1>Overview</h1><div class="sub">Fleet health, refreshed every 5 seconds.</div>
    <div class="grid cols-4">
      <div class="card tile"><div class="num">${s.online}<span class="muted">/${s.devices}</span></div><div class="label">devices online</div></div>
      <div class="card tile"><div class="num">${s.compliant}</div><div class="label">compliant · <span class="badge b-bad">${s.noncompliant} not</span> <span class="badge b-warn">${s.warning} warn</span></div></div>
      <div class="card tile"><div class="num">${s.sessions}</div><div class="label">agent desktops running</div></div>
      <div class="card tile"><div class="num">${s.laptops_online}</div><div class="label">agent laptops connected</div></div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Alerts</h2>${s.alerts.length ? s.alerts.map(a =>
        `<div class="alert ${a.level}"><b><a href="#/device/${encodeURIComponent(a.device)}">${esc(a.device)}</a></b> ${esc(a.text)}</div>`).join("")
        : `<div class="muted">No alerts. Everything is online and compliant.</div>`}</div>
      <div class="card"><h2>Session-host capacity</h2>${s.capacity.map(h => `
        <div style="margin-bottom:14px">
          <div class="row space"><a href="#/device/${encodeURIComponent(h.id)}"><b>${esc(h.id)}</b></a>
            <span class="muted">${h.online ? `${h.sessions} desktops · CPU ${pct(h.cpu)} · RAM ${pct(h.mem_pct)}` : "offline"}</span></div>
          ${spark(h.history)}
        </div>`).join("") || `<div class="muted">No session hosts reporting yet.</div>`}</div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Desktop catalog</h2>
      <div class="row">Pilot ring: <span class="badge b-info">v${s.catalog.pilot ?? "–"}</span>
      Broad ring: <span class="badge b-info">v${s.catalog.broad ?? "–"}</span>
      <a href="#/apps">Manage apps →</a></div></div>`;
}

// ---- devices ------------------------------------------------------------------
async function devices() {
  const list = await api("/api/devices");
  const filter = devices.filter || "";
  const rows = list.filter(d => !filter || d.role === filter);
  main.innerHTML = `
    <div class="row space"><div><h1>Devices</h1><div class="sub">Every machine that reports to the console.</div></div>
      <select id="role-filter"><option value="">All roles</option>${Object.entries(ROLE).map(([k, v]) =>
        `<option value="${k}" ${k === filter ? "selected" : ""}>${v}</option>`).join("")}</select></div>
    <div class="card"><table>
      <tr><th>Device</th><th>Role</th><th>Status</th><th>User / sessions</th><th>Compliance</th><th>CPU</th><th>RAM</th><th>Ring</th><th>Last seen</th></tr>
      ${rows.map(d => { const m = d.facts.metrics || {}; return `
      <tr class="click" data-id="${esc(d.id)}">
        <td><b>${esc(d.id)}</b><div class="muted">${esc(d.facts.os || "")}</div></td>
        <td>${ROLE[d.role] || esc(d.role)}</td><td>${onlineDot(d)}</td>
        <td>${d.role === "session_host" ? `${d.sessions.length} desktops` : esc(d.user || "–")}</td>
        <td>${stateBadge(d.compliance_state)}</td>
        <td style="width:90px">${m.cpu != null ? bar(m.cpu) : "–"}</td><td style="width:90px">${m.mem_pct != null ? bar(m.mem_pct) : "–"}</td>
        <td>${esc(d.ring)}</td><td class="muted">${ago(d.last_seen)}</td></tr>`; }).join("") ||
        `<tr><td colspan="9" class="muted">No devices yet. Agents check in every 15 seconds.</td></tr>`}
    </table></div>`;
  $("#role-filter").onchange = e => { devices.filter = e.target.value; devices(); };
  main.querySelectorAll("tr.click").forEach(tr => tr.onclick = () => location.hash = `#/device/${encodeURIComponent(tr.dataset.id)}`);
}

async function device(id) {
  if (!$("#modal").classList.contains("hidden")) return;   // don't redraw under an open live view
  const d = await api(`/api/devices/${encodeURIComponent(id)}`);
  const m = d.facts.metrics || {};
  const acts = Object.entries(d.actions).filter(([k]) => !["screenshot", "screenshot_user", "logoff_user"].includes(k));
  main.innerHTML = `
    <div class="row space">
      <div><h1>${esc(d.id)}</h1><div class="sub">${ROLE[d.role] || esc(d.role)} · ${onlineDot(d)} · last seen ${ago(d.last_seen)} · ${esc(d.ip || "")}</div></div>
      <div class="row">Ring <select id="ring">${["pilot", "broad"].map(r => `<option ${r === d.ring ? "selected" : ""}>${r}</option>`).join("")}</select></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><h2>Compliance ${stateBadge(d.compliance_state)}</h2><ul class="checks">${Object.entries(d.compliance).map(([k, c]) => `
        <li><span>${c.ok === true ? "✅" : c.ok === false ? (c.severity === "warn" ? "⚠️" : "❌") : "➖"}</span>
        <span><b>${esc(k.replace(/_/g, " "))}</b><br><span class="muted">${esc(c.detail)}</span></span></li>`).join("") || "<li class='muted'>No checks reported.</li>"}</ul></div>
      <div class="card"><h2>Health</h2>
        <div class="kv">
          <div>OS</div><div>${esc(d.facts.os || "–")}</div>
          ${d.facts.kernel ? `<div>Kernel</div><div>${esc(d.facts.kernel)}</div>` : ""}
          ${d.user ? `<div>Signed-in user</div><div>${esc(d.user)}</div>` : ""}
          ${d.facts.uptime_s != null ? `<div>Uptime</div><div>${Math.round(d.facts.uptime_s / 3600)} h</div>` : ""}
          ${d.facts.cpus ? `<div>CPU / RAM</div><div>${d.facts.cpus} cores · ${Math.round(d.facts.mem_mb / 1024)} GB</div>` : ""}
          <div>CPU now</div><div>${pct(m.cpu)}</div><div>Memory</div><div>${pct(m.mem_pct)}</div>
          ${m.disk_pct != null ? `<div>Disk</div><div>${pct(m.disk_pct)}</div>` : ""}
          ${d.role === "laptop" ? `<div>Desktop open</div><div>${d.facts.desktop_open ? "yes" : "no"}</div>
             <div>Genesys running</div><div>${d.facts.genesys_running ? "yes" : "no"}</div>` : ""}
          ${d.facts.simulated ? `<div>Note</div><div class="muted">simulated machine (container)</div>` : ""}
        </div>
        <div style="margin-top:10px">CPU, last 15 minutes ${spark(d.history)}</div></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Actions</h2><div class="row">
      ${d.role === "laptop" ? `<button class="primary" id="watch-laptop">👁 View laptop screen</button>` : ""}
      ${acts.map(([k, a]) => `<button data-action="${k}" class="${a.confirm ? "danger" : ""}">${esc(a.label)}</button>`).join("")}
    </div></div>
    ${d.role === "session_host" ? `<div class="card" style="margin-top:16px"><h2>Agent desktops on this host</h2>${sessionTable(d.sessions.map(s => ({ ...s, host: d.id })))}</div>` : ""}
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Timeline (what happened on the device)</h2><div class="timeline">${timeline(d.events)}</div></div>
      <div class="card"><h2>Recent commands</h2><table>${d.commands.map(c => `
        <tr><td class="muted">${time(c.created_at)}</td><td>${esc(c.action)}</td><td>${statusBadge(c.status)}</td>
        <td class="muted">${esc(c.created_by)}</td><td>${c.result && !c.has_image ? `<button class="link" data-result="${c.id}">output</button>` : ""}</td></tr>`).join("") ||
        "<tr><td class='muted'>None yet.</td></tr>"}</table></div>
    </div>`;
  $("#ring").onchange = async e => { await api(`/api/devices/${encodeURIComponent(id)}/ring`, { method: "POST", body: { ring: e.target.value } }); toast(`${id} moved to ${e.target.value}`); };
  if ($("#watch-laptop")) $("#watch-laptop").onclick = () => liveView(id, "screenshot", {}, `${id} · laptop screen`);
  main.querySelectorAll("[data-action]").forEach(b => b.onclick = () => runAction(id, b.dataset.action, d.actions[b.dataset.action]));
  main.querySelectorAll("[data-result]").forEach(b => b.onclick = async () => {
    const c = await api(`/api/commands/${b.dataset.result}`);
    openModal(`${c.action} · ${c.status}`, `<pre>${esc(c.result)}</pre>`);
  });
  bindSessionButtons();
}

function statusBadge(s) {
  return `<span class="badge ${{ done: "b-ok", failed: "b-bad", expired: "b-warn" }[s] || "b-info"}">${esc(s)}</span>`;
}
function timeline(events) {
  return events.map(e => `<div class="tl"><span class="k">${time(e.ts)}</span><span class="badge b-info">${esc(e.kind)}</span>
    <span>${esc(e.message)}${e.user ? ` <span class="muted">· ${esc(e.user)}</span>` : ""}</span></div>`).join("") || `<div class="muted">Nothing yet.</div>`;
}

async function runAction(dev, action, spec, args = {}) {
  for (const a of spec.args || []) {
    if (args[a]) continue;
    const v = prompt(a === "text" ? "Message to show to the agent:" : a === "app" ? "App to start (e.g. genesys):" :
      a === "service" ? "Service to restart:" : `${a}:`, a === "app" ? "genesys" : "");
    if (!v) return;
    args[a] = v;
  }
  if (spec.confirm && !confirm(`${spec.label} on ${dev}${args.user ? ` (${args.user})` : ""}?`)) return;
  const { id } = await api(`/api/devices/${encodeURIComponent(dev)}/actions`, { method: "POST", body: { action, args } });
  toast(`${spec.label}: sent`);
  const c = await waitCommand(id, 30000);
  toast(`${spec.label}: ${c.status}${c.result ? ` – ${String(c.result).slice(0, 80)}` : ""}`);
  if (action === "collect_logs" && c.status === "done") openModal(`Logs · ${dev}`, `<pre>${esc(c.result)}</pre>`);
}

async function waitCommand(id, ms) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    const c = await api(`/api/commands/${id}`);
    if (["done", "failed", "expired"].includes(c.status)) return c;
    await new Promise(r => setTimeout(r, 500));
  }
  return { status: "timeout" };
}

// ---- live view: the device side ------------------------------------------------
function openModal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() { liveToken++; $("#modal").classList.add("hidden"); }
$("#modal-close").onclick = closeModal;

async function liveView(dev, action, args, title) {
  const token = ++liveToken;
  openModal(title, `<div class="row space"><span><span class="live">● LIVE</span> <span id="live-status" class="muted">connecting…</span></span>
    <span class="muted">${action === "screenshot_user" ? "The agent is notified when viewing starts. " : ""}Every view is recorded in the audit log.</span></div>
    <img id="live-img" class="screen" alt="screen">`);
  let first = true;
  while (token === liveToken) {
    try {
      const { id } = await api(`/api/devices/${encodeURIComponent(dev)}/actions`, { method: "POST", body: { action, args: { ...args, first: first ? "1" : "0" } } });
      first = false;
      const c = await waitCommand(id, 20000);
      if (token !== liveToken) break;
      if (c.has_image) {
        $("#live-img").src = `/api/commands/${id}/image`;
        $("#live-status").textContent = `updated ${new Date().toLocaleTimeString()}`;
      } else {
        $("#live-status").textContent = `${c.status}: ${c.result || ""}`;
      }
    } catch (e) { if ($("#live-status")) $("#live-status").textContent = e.message; }
    await new Promise(r => setTimeout(r, 2000));
  }
}

// ---- sessions -----------------------------------------------------------------
function sessionTable(list) {
  if (!list.length) return `<div class="muted">No agent desktops running.</div>`;
  return `<table><tr><th>Agent</th><th>Session host</th><th>Laptop</th><th>Genesys</th><th>CPU</th><th>RAM</th><th>Since</th><th></th></tr>
    ${list.map(s => `<tr><td><b>${esc(s.user)}</b></td><td><a href="#/device/${encodeURIComponent(s.host)}">${esc(s.host)}</a></td>
      <td>${s.laptop ? `<a href="#/device/${encodeURIComponent(s.laptop)}">${esc(s.laptop)}</a>` : `<span class="muted">${s.laptop === undefined ? "" : "–"}</span>`}</td>
      <td>${s.genesys == null ? "–" : s.genesys ? "🟢 running" : "⚪ not running"}</td>
      <td>${pct(s.cpu)}</td><td>${s.mem_mb} MB</td><td class="muted">${esc(s.since)}</td>
      <td class="row"><button data-watch="${esc(s.host)}|${esc(s.user)}">👁 Watch</button>
        <button data-msg="${esc(s.host)}|${esc(s.user)}">Message</button>
        <button class="danger" data-logoff="${esc(s.host)}|${esc(s.user)}">Log off</button></td></tr>`).join("")}</table>`;
}
function bindSessionButtons() {
  main.querySelectorAll("[data-watch]").forEach(b => b.onclick = () => {
    const [host, user] = b.dataset.watch.split("|");
    liveView(host, "screenshot_user", { user }, `${user} on ${host} · agent desktop`);
  });
  main.querySelectorAll("[data-msg]").forEach(b => b.onclick = () => {
    const [host, user] = b.dataset.msg.split("|");
    runAction(host, "message", { label: "Send message", args: ["text"] }, { user });
  });
  main.querySelectorAll("[data-logoff]").forEach(b => b.onclick = () => {
    const [host, user] = b.dataset.logoff.split("|");
    runAction(host, "logoff_user", { label: "Log user off", args: ["user"], confirm: true }, { user });
  });
}
async function sessions() {
  if (!$("#modal").classList.contains("hidden")) return;
  const list = await api("/api/sessions");
  main.innerHTML = `<h1>Sessions</h1><div class="sub">Every agent desktop on the farm, with the laptop it is viewed from.</div>
    <div class="card">${sessionTable(list)}</div>`;
  bindSessionButtons();
}

// ---- apps & desktop (catalog, rings) --------------------------------------------
async function apps() {
  const [cat, devs] = await Promise.all([api("/api/catalog"), api("/api/devices")]);
  const current = cat.versions.find(v => v.version === cat.rings.pilot) || { items: [] };
  apps.items = apps.items || current.items.map(i => ({ ...i }));
  main.innerHTML = `
    <h1>Apps &amp; desktop</h1>
    <div class="sub">The web systems on every agent desktop: one icon, one browser tab and one bookmark each.
      Publishing goes to the <b>pilot</b> ring first; promote it to <b>broad</b> when it looks right. Any version can be rolled back.</div>
    <div class="card catalog"><div class="row space"><h2>Systems (editing from v${current.version})</h2>
      <div class="row"><button id="add">+ Add system</button><button class="primary" id="publish">Publish to pilot</button></div></div>
      <table><tr><th>Name</th><th>URL</th><th>Icon</th><th></th></tr>
      ${apps.items.map((it, i) => `<tr>
        <td><input data-i="${i}" data-k="name" value="${esc(it.name)}"></td>
        <td><input data-i="${i}" data-k="url" value="${esc(it.url)}"></td>
        <td><input data-i="${i}" data-k="icon" value="${esc(it.icon || "")}" placeholder="applications-internet"></td>
        <td><button class="link danger" data-del="${i}">Remove</button></td></tr>`).join("")}</table></div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Rings</h2>
        <div class="row" style="margin-bottom:10px">Pilot: <span class="badge b-info">v${cat.rings.pilot}</span>
          Broad: <span class="badge b-info">v${cat.rings.broad}</span>
          <button id="promote" ${cat.rings.pilot === cat.rings.broad ? "disabled" : ""}>Promote pilot → broad</button></div>
        <table><tr><th>Session host</th><th>Ring</th><th>Status</th></tr>${devs.filter(d => d.role === "session_host").map(d => `
          <tr><td><a href="#/device/${encodeURIComponent(d.id)}">${esc(d.id)}</a></td><td>${esc(d.ring)}</td><td>${onlineDot(d)}</td></tr>`).join("")}</table>
        <p class="muted">Move a host between rings on its device page.</p></div>
      <div class="card"><h2>Versions</h2><table><tr><th>Version</th><th>By</th><th>When</th><th>Systems</th><th></th></tr>
        ${cat.versions.map(v => `<tr><td><b>v${v.version}</b> ${v.version === cat.rings.broad ? `<span class="badge b-ok">broad</span>` : ""}
          ${v.version === cat.rings.pilot ? `<span class="badge b-info">pilot</span>` : ""}</td><td>${esc(v.created_by)}</td>
          <td class="muted">${dateTime(v.created_at)}</td><td>${v.items.length}</td>
          <td><button class="link" data-rollback="${v.version}">Roll back to this</button></td></tr>`).join("")}</table></div>
    </div>`;
  main.querySelectorAll("input[data-i]").forEach(inp => inp.oninput = () => { apps.items[inp.dataset.i][inp.dataset.k] = inp.value; });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { apps.items.splice(+b.dataset.del, 1); apps(); });
  $("#add").onclick = () => { apps.items.push({ name: "", url: "https://", icon: "applications-internet" }); apps(); };
  $("#publish").onclick = async () => {
    try {
      const r = await api("/api/catalog", { method: "POST", body: { items: apps.items } });
      toast(`v${r.version} published to the pilot ring`); apps.items = null; apps();
    } catch (e) { toast(e.message); }
  };
  $("#promote").onclick = async () => { const r = await api("/api/catalog/promote", { method: "POST" }); toast(`v${r.version} is now on every host`); apps(); };
  main.querySelectorAll("[data-rollback]").forEach(b => b.onclick = async () => {
    if (!confirm(`Roll back both rings to v${b.dataset.rollback}?`)) return;
    await api("/api/catalog/rollback", { method: "POST", body: { version: +b.dataset.rollback } });
    toast(`rolled back to v${b.dataset.rollback}`); apps.items = null; apps();
  });
}

// ---- activity -----------------------------------------------------------------
async function activity() {
  const tab = activity.tab || "events";
  const rows = tab === "events" ? await api("/api/events?limit=300") : await api("/api/audit");
  main.innerHTML = `<h1>Activity</h1><div class="sub">What happened on the devices, and what admins did.</div>
    <div class="row" style="margin-bottom:12px"><button id="t-events" class="${tab === "events" ? "primary" : ""}">Device timeline</button>
      <button id="t-audit" class="${tab === "audit" ? "primary" : ""}">Admin audit log</button></div>
    <div class="card"><table>${tab === "events"
      ? `<tr><th>Time</th><th>Device</th><th>Kind</th><th>What happened</th><th>User</th></tr>${rows.map(e => `<tr><td class="muted">${dateTime(e.ts)}</td>
         <td><a href="#/device/${encodeURIComponent(e.device)}">${esc(e.device)}</a></td><td><span class="badge b-info">${esc(e.kind)}</span></td>
         <td>${esc(e.message)}</td><td>${esc(e.user || "")}</td></tr>`).join("")}`
      : `<tr><th>Time</th><th>Admin</th><th>Action</th><th>Target</th><th>Detail</th></tr>${rows.map(a => `<tr><td class="muted">${dateTime(a.ts)}</td>
         <td>${esc(a.admin)}</td><td>${esc(a.action)}</td><td>${esc(a.target)}</td><td class="muted">${esc(a.detail)}</td></tr>`).join("")}`}</table></div>`;
  $("#t-events").onclick = () => { activity.tab = "events"; activity(); };
  $("#t-audit").onclick = () => { activity.tab = "audit"; activity(); };
}

// ---- start -------------------------------------------------------------------
api("/api/me").then(r => start(r.user)).catch(() => showLogin());
