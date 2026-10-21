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
const icon = (name, cls = "") => `<svg class="i ${cls}"><use href="#i-${name}"/></svg>`;
const ROLE = { session_host: "Session host", broker: "Broker", endpoint: "Endpoint", laptop: "Agent laptop" };
const ROLE_ICON = { session_host: "server", broker: "server", endpoint: "devices", laptop: "laptop" };
const STATE = {
  compliant: ["b-ok", "Compliant"], warning: ["b-warn", "Warnings"],
  noncompliant: ["b-bad", "Not compliant"], unknown: ["b-plain", "Unknown"],
};
function stateBadge(s) { const [c, t] = STATE[s] || STATE.unknown; return `<span class="badge ${c}">${t}</span>`; }
function onlineDot(d) { return `<span class="dot ${d.online ? "on" : "off"}"></span>${d.online ? "Online" : "Offline"}`; }
function pct(v) { return v == null ? "–" : `${Math.round(v)}%`; }
function meter(v) {
  if (v == null) return `<span class="faint">–</span>`;
  return `<div class="meter"><div class="bar ${v > 85 ? "hot" : ""}"><span style="width:${Math.min(100, v)}%"></span></div><span class="v">${pct(v)}</span></div>`;
}
function spark(hist, idx = 1) {
  const pts = (hist || []).map(h => h[idx]).filter(v => v != null);
  if (pts.length < 2) return `<div class="faint" style="margin-top:6px">collecting…</div>`;
  const w = 300, h = 46, step = w / (pts.length - 1);
  const xy = pts.map((v, i) => [(i * step).toFixed(1), (h - 2 - (Math.min(v, 100) / 100) * (h - 4)).toFixed(1)]);
  const line = xy.map(p => p.join(",")).join(" ");
  const id = `g${Math.random().toString(36).slice(2, 8)}`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".5"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,${h} ${line} ${w},${h}" style="fill:url(#${id})"/><polyline points="${line}"/></svg>`;
}
function head(title, sub, right = "", ico = "") {
  return `<div class="page-head"><div><h1>${ico ? icon(ico) : ""}${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>
    ${right ? `<div class="row">${right}</div>` : ""}</div>`;
}
function devCell(d, sub = "") {
  return `<div class="dev"><span class="ico">${icon(ROLE_ICON[d.role] || "devices")}</span>
    <div><b>${esc(d.id)}</b><div class="muted" style="font-size:12.5px">${sub || esc(d.facts?.os || ROLE[d.role] || "")}</div></div></div>`;
}
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const devLink = id => `<a href="#/device/${encodeURIComponent(id)}">${esc(id)}</a>`;

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
  $("#avatar").textContent = user.replace(/[^a-z]/gi, "").slice(0, 2) || "?";
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  route();
}

// ---- routing ----------------------------------------------------------------
const pages = { overview, devices, device, sessions, apps, policies, activity };
function route() {
  clearInterval(refreshTimer);
  const [, page = "overview", arg] = location.hash.split("/");
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page ||
    (page === "device" && a.dataset.page === "devices")));
  const fn = pages[page] || overview;
  const run = () => fn(arg ? decodeURIComponent(arg) : undefined).catch(e => {
    if (e.message !== "signed out") main.innerHTML = `<div class="card alert critical">${icon("alert")}${esc(e.message)}</div>`;
  });
  run();
  if (["overview", "devices", "device", "sessions"].includes(page)) refreshTimer = setInterval(run, 5000);
}
window.addEventListener("hashchange", route);

// ---- overview -----------------------------------------------------------------
async function overview() {
  const s = await api("/api/summary");
  const kpi = (ico, cls, num, label) => `<div class="card kpi"><span class="ico ${cls}">${icon(ico)}</span>
    <div><div class="num">${num}</div><div class="label">${label}</div></div></div>`;
  main.innerHTML = `
    ${head("Overview", "Fleet health at a glance. Refreshes every 5 seconds.")}
    <div class="grid cols-4">
      ${kpi("devices", "", `${s.online}<small> / ${s.devices}</small>`, "devices online")}
      ${kpi("check", "ok", s.compliant, `compliant · <span class="badge b-bad">${s.noncompliant} not</span> <span class="badge b-warn">${s.warning} warn</span>`)}
      ${kpi("sessions", "violet", s.sessions, "agent desktops running")}
      ${kpi("laptop", "warn", s.laptops_online, "agent laptops connected")}
    </div>
    <div class="grid cols-3-1 mt">
      <div class="card"><div class="card-head"><h2>${icon("server")}Session-host capacity</h2><span class="muted">CPU, last 15 minutes</span></div>
        ${s.capacity.map(h => `<div class="host">
          <div class="row space"><b>${devLink(h.id)}</b>
            <span class="row">${h.online ? `<span class="badge b-plain">${h.sessions} desktops</span>
              <span class="muted">CPU</span>${meter(h.cpu)}<span class="muted">RAM</span>${meter(h.mem_pct)}` : `<span class="badge b-bad">offline</span>`}</span></div>
          ${spark(h.history)}</div>`).join("") || `<div class="empty">No session hosts reporting yet.</div>`}</div>
      <div class="card"><div class="card-head"><h2>${icon("alert")}Alerts</h2><span class="badge ${s.alerts.length ? "b-warn" : "b-ok"}">${s.alerts.length}</span></div>
        ${s.alerts.length ? s.alerts.map(a =>
          `<div class="alert ${a.level}">${icon("alert")}<span><b>${devLink(a.device)}</b> ${esc(a.text)}</span></div>`).join("")
          : `<div class="empty">${icon("check")}No alerts. Everything is online and compliant.</div>`}</div>
    </div>
    <div class="card mt"><div class="card-head"><h2>${icon("rocket")}Rollout</h2></div>
      <div class="grid cols-2">
        <div class="ring-strip"><span class="muted" style="width:120px">Desktop catalog</span>
          <span class="ring">Pilot <b>v${s.catalog.pilot ?? "–"}</b></span><span class="ring">Broad <b>v${s.catalog.broad ?? "–"}</b></span>
          <a href="#/apps">Manage →</a></div>
        <div class="ring-strip"><span class="muted" style="width:120px">Policies</span>
          <span class="ring">Pilot <b>v${s.policy?.pilot ?? "–"}</b></span><span class="ring">Broad <b>v${s.policy?.broad ?? "–"}</b></span>
          <a href="#/policies">Manage →</a></div>
      </div></div>`;
}

// ---- devices ------------------------------------------------------------------
async function devices() {
  const list = await api("/api/devices");
  const filter = devices.filter || "";
  const rows = list.filter(d => !filter || d.role === filter);
  main.innerHTML = `
    ${head("Devices", "Every machine that reports to the console.",
      `<select id="role-filter"><option value="">All roles</option>${Object.entries(ROLE).map(([k, v]) =>
        `<option value="${k}" ${k === filter ? "selected" : ""}>${v}</option>`).join("")}</select>`)}
    <div class="card flush"><div class="table-wrap"><table>
      <thead><tr><th>Device</th><th>Role</th><th>Status</th><th>User / sessions</th><th>Compliance</th><th>CPU</th><th>RAM</th><th>Ring</th><th>Last seen</th></tr></thead>
      <tbody>${rows.map(d => { const m = d.facts.metrics || {}; return `
      <tr class="click" data-id="${esc(d.id)}">
        <td>${devCell(d)}</td>
        <td>${ROLE[d.role] || esc(d.role)}</td><td>${onlineDot(d)}</td>
        <td>${d.role === "session_host" ? `${d.sessions.length} desktops` : esc(d.user || "–")}</td>
        <td>${stateBadge(d.compliance_state)}</td>
        <td>${meter(m.cpu)}</td><td>${meter(m.mem_pct)}</td>
        <td><span class="badge ${d.ring === "pilot" ? "b-violet" : "b-plain"}">${esc(d.ring)}</span></td>
        <td class="muted">${ago(d.last_seen)}</td></tr>`; }).join("") ||
        `<tr><td colspan="9" class="muted">No devices yet. Agents check in every 15 seconds.</td></tr>`}</tbody>
    </table></div></div>`;
  $("#role-filter").onchange = e => { devices.filter = e.target.value; devices(); };
  main.querySelectorAll("tr.click").forEach(tr => tr.onclick = () => location.hash = `#/device/${encodeURIComponent(tr.dataset.id)}`);
}

function checkIcon(c) {
  if (c.ok === true) return `<span class="chk ok">${icon("check")}</span>`;
  if (c.ok === false) return c.severity === "warn" ? `<span class="chk warn">${icon("alert")}</span>` : `<span class="chk bad">${icon("x")}</span>`;
  return `<span class="chk na">–</span>`;
}

async function device(id) {
  if (!$("#modal").classList.contains("hidden")) return;   // don't redraw under an open live view
  const d = await api(`/api/devices/${encodeURIComponent(id)}`);
  const m = d.facts.metrics || {};
  const pol = d.facts.policy;
  const acts = Object.entries(d.actions).filter(([k]) => !["screenshot", "screenshot_user", "logoff_user"].includes(k));
  main.innerHTML = `
    ${head(esc(d.id), `${ROLE[d.role] || esc(d.role)} · ${onlineDot(d)} · last seen ${ago(d.last_seen)} · ${esc(d.ip || "")}`,
      `<span class="muted">Ring</span><select id="ring">${["pilot", "broad"].map(r => `<option ${r === d.ring ? "selected" : ""}>${r}</option>`).join("")}</select>`,
      ROLE_ICON[d.role] || "devices")}
    <div class="grid cols-2">
      <div class="card"><div class="card-head"><h2>Compliance</h2>${stateBadge(d.compliance_state)}</div>
        <ul class="checks">${Object.entries(d.compliance).map(([k, c]) => `
        <li>${checkIcon(c)}<span><b>${esc(k.replace(/_/g, " "))}</b><br><span class="muted">${esc(c.detail)}</span></span></li>`).join("") || "<li class='muted'>No checks reported.</li>"}</ul></div>
      <div class="card"><div class="card-head"><h2>Health</h2></div>
        <div class="kv">
          <div>OS</div><div>${esc(d.facts.os || "–")}</div>
          ${d.facts.kernel ? `<div>Kernel</div><div class="mono">${esc(d.facts.kernel)}</div>` : ""}
          ${d.user ? `<div>Signed-in user</div><div>${esc(d.user)}</div>` : ""}
          ${d.facts.uptime_s != null ? `<div>Uptime</div><div>${Math.round(d.facts.uptime_s / 3600)} h</div>` : ""}
          ${d.facts.cpus ? `<div>CPU / RAM</div><div>${d.facts.cpus} cores · ${Math.round(d.facts.mem_mb / 1024)} GB</div>` : ""}
          <div>CPU now</div><div>${meter(m.cpu)}</div><div>Memory</div><div>${meter(m.mem_pct)}</div>
          ${m.disk_pct != null ? `<div>Disk</div><div>${meter(m.disk_pct)}</div>` : ""}
          ${pol ? `<div>Policies</div><div><span class="badge ${pol.error ? "b-bad" : "b-ok"}">v${pol.version}</span>
            <span class="muted">URLs ${pol.url_mode === "allow_only" ? "allow-only" : "block list"} · firewall ${pol.egress_mode === "allow_only" ? `allow-only (${plural(pol.egress_rules, "rule")})` : "off"}</span></div>` : ""}
          ${d.role === "laptop" ? `<div>Desktop open</div><div>${d.facts.desktop_open ? "yes" : "no"}</div>
             <div>Genesys running</div><div>${d.facts.genesys_running ? "yes" : "no"}</div>` : ""}
          ${d.facts.simulated ? `<div>Note</div><div class="muted">simulated machine (container)</div>` : ""}
        </div>
        <div class="field-label">CPU, last 15 minutes</div>${spark(d.history)}</div>
    </div>
    <div class="card mt"><div class="card-head"><h2>Actions</h2></div><div class="action-grid">
      ${d.role === "laptop" ? `<button class="primary" id="watch-laptop">${icon("eye")}View laptop screen</button>` : ""}
      ${acts.map(([k, a]) => `<button data-action="${k}" class="${a.confirm ? "danger" : ""}">${esc(a.label)}</button>`).join("")}
    </div></div>
    ${d.role === "session_host" ? `<div class="card flush mt"><div class="card-head"><h2>${icon("sessions")}Agent desktops on this host</h2></div>${sessionTable(d.sessions.map(s => ({ ...s, host: d.id })))}</div>` : ""}
    <div class="grid cols-2 mt">
      <div class="card"><div class="card-head"><h2>${icon("activity")}Timeline</h2><span class="muted">what happened on the device</span></div><div class="timeline">${timeline(d.events)}</div></div>
      <div class="card flush"><div class="card-head"><h2>Recent commands</h2></div><div class="table-wrap"><table><tbody>${d.commands.map(c => `
        <tr><td class="muted">${time(c.created_at)}</td><td>${esc(c.action)}</td><td>${statusBadge(c.status)}</td>
        <td class="muted">${esc(c.created_by)}</td><td>${c.result && !c.has_image ? `<button class="link" data-result="${c.id}">output</button>` : ""}</td></tr>`).join("") ||
        "<tr><td class='muted'>None yet.</td></tr>"}</tbody></table></div></div>
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
  return events.map(e => `<div class="tl"><span class="k">${time(e.ts)}</span><span><span class="badge b-info">${esc(e.kind)}</span></span>
    <span>${esc(e.message)}${e.user ? ` <span class="muted">· ${esc(e.user)}</span>` : ""}</span></div>`).join("") || `<div class="empty">Nothing yet.</div>`;
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
  openModal(title, `<div class="row space" style="margin-bottom:10px"><span><span class="live">● LIVE</span> <span id="live-status" class="muted">connecting…</span></span>
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
  if (!list.length) return `<div class="empty" style="padding:16px 20px">No agent desktops running.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Session host</th><th>Laptop</th><th>Genesys</th><th>CPU</th><th>RAM</th><th>Since</th><th></th></tr></thead>
    <tbody>${list.map(s => `<tr><td><div class="dev"><span class="avatar" style="width:30px;height:30px;font-size:11px">${esc(s.user.slice(0, 2))}</span><b>${esc(s.user)}</b></div></td>
      <td>${devLink(s.host)}</td>
      <td>${s.laptop ? devLink(s.laptop) : `<span class="faint">${s.laptop === undefined ? "" : "–"}</span>`}</td>
      <td>${s.genesys == null ? `<span class="faint">–</span>` : s.genesys ? `<span class="badge b-ok">running</span>` : `<span class="badge b-plain">not running</span>`}</td>
      <td>${meter(s.cpu)}</td><td class="mono">${s.mem_mb} MB</td><td class="muted">${esc(s.since)}</td>
      <td class="actions"><div class="row"><button data-watch="${esc(s.host)}|${esc(s.user)}">${icon("eye")}Watch</button>
        <button data-msg="${esc(s.host)}|${esc(s.user)}">${icon("msg")}Message</button>
        <button class="danger" data-logoff="${esc(s.host)}|${esc(s.user)}">${icon("power")}Log off</button></div></td></tr>`).join("")}</tbody></table></div>`;
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
  main.innerHTML = `${head("Sessions", "Every agent desktop on the farm, with the laptop it is viewed from.")}
    <div class="card flush">${sessionTable(list)}</div>`;
  bindSessionButtons();
}

// ---- rings: shared by apps and policies -----------------------------------------
function ringsCard(kind, data, devs, countLabel) {
  return `<div class="card flush"><div class="card-head"><h2>${icon("rocket")}Rings</h2>
      <div class="ring-strip"><span class="ring">Pilot <b>v${data.rings.pilot}</b></span><span class="ring">Broad <b>v${data.rings.broad}</b></span>
      <button class="primary" id="promote" ${data.rings.pilot === data.rings.broad ? "disabled" : ""}>Promote pilot → broad</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>Version</th><th>By</th><th>When</th><th>${countLabel}</th><th></th></tr></thead><tbody>
      ${data.versions.map(v => `<tr><td><b>v${v.version}</b> ${v.version === data.rings.broad ? `<span class="badge b-ok">broad</span>` : ""}
        ${v.version === data.rings.pilot ? `<span class="badge b-violet">pilot</span>` : ""}<div class="muted" style="font-size:12.5px">${esc(v.note || "")}</div></td>
        <td>${esc(v.created_by)}</td><td class="muted">${dateTime(v.created_at)}</td><td>${devs(v)}</td>
        <td><button class="link" data-rollback="${v.version}">${icon("undo")}Roll back</button></td></tr>`).join("")}</tbody></table></div></div>`;
}
function bindRings(kind, redraw) {
  $("#promote").onclick = async () => { const r = await api(`/api/${kind}/promote`, { method: "POST" }); toast(`v${r.version} is now on every device`); redraw(); };
  main.querySelectorAll("[data-rollback]").forEach(b => b.onclick = async () => {
    if (!confirm(`Roll back both rings to v${b.dataset.rollback}?`)) return;
    await api(`/api/${kind}/rollback`, { method: "POST", body: { version: +b.dataset.rollback } });
    toast(`rolled back to v${b.dataset.rollback}`); redraw(true);
  });
}

// ---- apps & desktop (catalog, rings) --------------------------------------------
async function apps(reset) {
  const [cat, devs] = await Promise.all([api("/api/catalog"), api("/api/devices")]);
  const current = cat.versions.find(v => v.version === cat.rings.pilot) || { items: [] };
  if (reset) apps.items = null;
  apps.items = apps.items || current.items.map(i => ({ ...i }));
  main.innerHTML = `
    ${head("Apps &amp; desktop", `The web systems on every agent desktop: one tile, one browser tab and one bookmark each.
      Publishing goes to the <b>pilot</b> ring first; promote it to <b>broad</b> when it looks right.`, "", "apps")}
    <div class="grid cols-3-1">
      <div class="card flush editor"><div class="card-head"><h2>Systems <span class="badge b-plain">editing from v${current.version}</span></h2>
        <button id="add">${icon("plus")}Add system</button></div>
        <div class="table-wrap"><table><thead><tr><th>Name</th><th>URL</th><th>Icon</th><th></th></tr></thead><tbody>
        ${apps.items.map((it, i) => `<tr>
          <td><input data-i="${i}" data-k="name" value="${esc(it.name)}"></td>
          <td><input data-i="${i}" data-k="url" value="${esc(it.url)}"></td>
          <td><input data-i="${i}" data-k="icon" value="${esc(it.icon || "")}" placeholder="applications-internet"></td>
          <td><button class="icon-btn danger" data-del="${i}" title="Remove">${icon("x")}</button></td></tr>`).join("")}</tbody></table></div></div>
      <div class="card flush"><div class="card-head"><h2>${icon("server")}Session hosts</h2></div>
        <div class="table-wrap"><table><tbody>${devs.filter(d => d.role === "session_host").map(d => `
          <tr><td>${devLink(d.id)}</td><td><span class="badge ${d.ring === "pilot" ? "b-violet" : "b-plain"}">${esc(d.ring)}</span></td><td>${onlineDot(d)}</td></tr>`).join("")}</tbody></table></div>
        <p class="muted" style="padding:0 20px">Move a host between rings on its device page.</p></div>
    </div>
    <div class="publish-bar"><span class="muted">${apps.items.length} systems</span><input id="note" placeholder="What changed? (shown in the version history)">
      <button class="primary" id="publish">${icon("rocket")}Publish to pilot</button></div>
    <div class="mt">${ringsCard("catalog", cat, v => plural(v.items.length, "system"), "Systems")}</div>`;
  main.querySelectorAll("input[data-i]").forEach(inp => inp.oninput = () => { apps.items[inp.dataset.i][inp.dataset.k] = inp.value; });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { apps.items.splice(+b.dataset.del, 1); apps(); });
  $("#add").onclick = () => { apps.items.push({ name: "", url: "https://", icon: "applications-internet" }); apps(); };
  $("#publish").onclick = async () => {
    try {
      const r = await api("/api/catalog", { method: "POST", body: { items: apps.items, note: $("#note").value } });
      toast(`v${r.version} published to the pilot ring`); apps(true);
    } catch (e) { toast(e.message); }
  };
  bindRings("catalog", apps);
}

// ---- policies: browser URL rules + outbound firewall ------------------------------
async function policies(reset) {
  const data = await api("/api/policy");
  const current = data.versions.find(v => v.version === data.rings.pilot) || { policy: {} };
  if (reset) policies.draft = null;
  const p = policies.draft = policies.draft || JSON.parse(JSON.stringify({
    url_mode: "block_listed", url_block: [], url_allow: [], egress_mode: "off", egress_allow: [], ...current.policy }));
  const chips = (key, cls) => `<div class="chips">${p[key].map((u, i) =>
    `<span class="chip ${cls}">${esc(u)}<button data-unchip="${key}|${i}" title="Remove">${icon("x")}</button></span>`).join("") ||
    `<span class="faint">None.</span>`}</div>
    <div class="add-row"><input data-chip-input="${key}" placeholder="${key === "url_block" ? "example.com or *.example.com/path" : "intranet.example.com"}">
    <button data-chip-add="${key}">${icon("plus")}Add</button></div>`;
  const allowOnly = p.url_mode === "allow_only";
  const fwOn = p.egress_mode === "allow_only";
  main.innerHTML = `
    ${head("Policies", `Browser and network rules for agents on session hosts and endpoints. Like the desktop catalog,
      a new version goes to the <b>pilot</b> ring first and can be promoted or rolled back.`, "", "policy")}
    <div class="grid cols-2">
      <div class="card"><div class="card-head"><h2>${icon("globe")}Browser · URL rules</h2>
        <div class="seg"><button data-urlmode="block_listed" class="${allowOnly ? "" : "on"}">Block listed sites</button>
          <button data-urlmode="allow_only" class="${allowOnly ? "on" : ""}">Allow only listed sites</button></div></div>
        ${allowOnly ? `<div class="field-label">Allowed sites</div>${chips("url_allow", "allow")}
            <div class="note">${icon("check")}<span>Every system in the desktop catalog and Genesys are always allowed, so the agent desktop keeps working.</span></div>`
          : `<div class="field-label">Blocked sites</div>${chips("url_block", "block")}
             <div class="field-label">Exceptions (always allowed)</div>${chips("url_allow", "allow")}
             <div class="note">${icon("policy")}<span>Added on top of the base lock-down (no <code>file://</code>, no <code>chrome://flags</code>). Uses Chrome's URL filter format.</span></div>`}
      </div>
      <div class="card"><div class="card-head"><h2>${icon("wall")}Outbound firewall</h2>
        <div class="seg"><button data-fw="off" class="${fwOn ? "" : "on"}">Off</button>
          <button data-fw="allow_only" class="${fwOn ? "on" : ""}">Allow only listed destinations</button></div></div>
        ${fwOn ? `<div class="table-wrap editor"><table><thead><tr><th>Name</th><th>Destination (IP / CIDR)</th><th>Protocol</th><th>Ports</th><th></th></tr></thead><tbody>
          ${p.egress_allow.map((r, i) => `<tr>
            <td><input data-rule="${i}" data-k="name" value="${esc(r.name)}" placeholder="CRM"></td>
            <td><input data-rule="${i}" data-k="dest" value="${esc(r.dest)}" placeholder="10.10.0.0/24" class="mono"></td>
            <td><select data-rule="${i}" data-k="proto">${["tcp", "udp"].map(x => `<option ${x === r.proto ? "selected" : ""}>${x}</option>`).join("")}</select></td>
            <td><input data-rule="${i}" data-k="ports" value="${esc(r.ports)}" placeholder="any, or 80,443" class="mono"></td>
            <td><button class="icon-btn danger" data-delrule="${i}" title="Remove">${icon("x")}</button></td></tr>`).join("") ||
            `<tr><td colspan="5" class="muted">No destinations yet: agents' apps could reach nothing.</td></tr>`}</tbody></table></div>
          <button id="addrule" style="margin-top:10px">${icon("plus")}Add destination</button>`
          : `<div class="empty">Agents' apps may connect anywhere the network allows.</div>`}
        <div class="note">${icon("policy")}<span>Only agents' own apps are filtered. The machine itself (sign-in, home folders, updates, this console) is never blocked, so a wrong rule cannot cut a device off.</span></div>
      </div>
    </div>
    <div class="publish-bar"><span class="muted">Editing from v${current.version}</span><input id="note" placeholder="What changed? (shown in the version history)">
      <button id="discard">Discard changes</button><button class="primary" id="publish">${icon("rocket")}Publish to pilot</button></div>
    <div class="grid cols-2 mt">
      <div class="card flush"><div class="card-head"><h2>${icon("devices")}Devices</h2><span class="muted">what each device has applied</span></div>
        <div class="table-wrap"><table><thead><tr><th>Device</th><th>Ring</th><th>Applied</th><th>URLs</th><th>Firewall</th></tr></thead><tbody>
        ${data.devices.map(d => { const a = d.applied; const want = data.rings[d.ring]; return `<tr>
          <td><span class="dot ${d.online ? "on" : "off"}" title="${d.online ? "online" : "offline"}"></span>${devLink(d.id)}</td><td><span class="badge ${d.ring === "pilot" ? "b-violet" : "b-plain"}">${esc(d.ring)}</span></td>
          <td>${!a ? `<span class="badge b-plain">waiting</span>` : a.error ? `<span class="badge b-bad" title="${esc(a.error)}">v${a.version} failed</span>`
            : `<span class="badge ${a.version === want ? "b-ok" : "b-warn"}">v${a.version}${a.version === want ? "" : ` → v${want}`}</span>`}</td>
          <td class="muted">${a ? (a.url_mode === "allow_only" ? "allow-only" : `block list (${a.url_rules})`) : ""}</td>
          <td class="muted">${a ? (a.egress_mode === "allow_only" ? `allow-only (${a.egress_rules})` : "off") : ""}</td></tr>`; }).join("") ||
          `<tr><td colspan="5" class="muted">No session hosts or endpoints reporting yet.</td></tr>`}</tbody></table></div></div>
      ${ringsCard("policy", data, v => `URLs ${v.policy.url_mode === "allow_only" ? "allow-only" : `${v.policy.url_block.length} blocked`} · firewall ${v.policy.egress_mode === "allow_only" ? plural(v.policy.egress_allow.length, "rule") : "off"}`, "Rules")}
    </div>`;

  main.querySelectorAll("[data-urlmode]").forEach(b => b.onclick = () => { p.url_mode = b.dataset.urlmode; policies(); });
  main.querySelectorAll("[data-fw]").forEach(b => b.onclick = () => { p.egress_mode = b.dataset.fw; policies(); });
  main.querySelectorAll("[data-unchip]").forEach(b => b.onclick = () => { const [k, i] = b.dataset.unchip.split("|"); p[k].splice(+i, 1); policies(); });
  const addChip = key => {
    const inp = main.querySelector(`[data-chip-input="${key}"]`);
    const v = inp.value.trim();
    if (v && !p[key].includes(v)) p[key].push(v);
    policies();
    main.querySelector(`[data-chip-input="${key}"]`)?.focus();
  };
  main.querySelectorAll("[data-chip-add]").forEach(b => b.onclick = () => addChip(b.dataset.chipAdd));
  main.querySelectorAll("[data-chip-input]").forEach(inp => inp.onkeydown = e => { if (e.key === "Enter") addChip(inp.dataset.chipInput); });
  main.querySelectorAll("[data-rule]").forEach(inp => inp.oninput = inp.onchange = () => { p.egress_allow[+inp.dataset.rule][inp.dataset.k] = inp.value; });
  main.querySelectorAll("[data-delrule]").forEach(b => b.onclick = () => { p.egress_allow.splice(+b.dataset.delrule, 1); policies(); });
  if ($("#addrule")) $("#addrule").onclick = () => { p.egress_allow.push({ name: "", dest: "", proto: "tcp", ports: "" }); policies(); };
  $("#discard").onclick = () => policies(true);
  $("#publish").onclick = async () => {
    try {
      const r = await api("/api/policy", { method: "POST", body: { ...p, note: $("#note").value } });
      toast(`policies v${r.version} published to the pilot ring`); policies(true);
    } catch (e) { toast(e.message); }
  };
  bindRings("policy", policies);
}

// ---- activity -----------------------------------------------------------------
async function activity() {
  const tab = activity.tab || "events";
  const rows = tab === "events" ? await api("/api/events?limit=300") : await api("/api/audit");
  main.innerHTML = `${head("Activity", "What happened on the devices, and what admins did.",
      `<div class="seg"><button id="t-events" class="${tab === "events" ? "on" : ""}">Device timeline</button>
       <button id="t-audit" class="${tab === "audit" ? "on" : ""}">Admin audit log</button></div>`)}
    <div class="card flush"><div class="table-wrap"><table>${tab === "events"
      ? `<thead><tr><th>Time</th><th>Device</th><th>Kind</th><th>What happened</th><th>User</th></tr></thead><tbody>${rows.map(e => `<tr><td class="muted">${dateTime(e.ts)}</td>
         <td>${devLink(e.device)}</td><td><span class="badge b-info">${esc(e.kind)}</span></td>
         <td>${esc(e.message)}</td><td>${esc(e.user || "")}</td></tr>`).join("")}</tbody>`
      : `<thead><tr><th>Time</th><th>Admin</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>${rows.map(a => `<tr><td class="muted">${dateTime(a.ts)}</td>
         <td><b>${esc(a.admin)}</b></td><td>${esc(a.action)}</td><td>${esc(a.target)}</td><td class="muted">${esc(a.detail)}</td></tr>`).join("")}</tbody>`}</table></div></div>`;
  $("#t-events").onclick = () => { activity.tab = "events"; activity(); };
  $("#t-audit").onclick = () => { activity.tab = "audit"; activity(); };
}

// ---- start -------------------------------------------------------------------
api("/api/me").then(r => start(r.user)).catch(() => showLogin());
