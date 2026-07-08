"""Static HTML for the web UI: a login page and a single-page dashboard.

The dashboard shell is a constant; it fetches everything it shows from the JSON
API in server.py. Kept dependency-free (inline CSS + vanilla JS) so it needs no
build step and no external assets — matching the existing /upload page.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

_STYLE = """
:root { color-scheme: dark; --bg:#14161a; --card:#1e2127; --line:#2c313a;
  --fg:#e8eaed; --mut:#a5abb5; --accent:#3b82f6; --ok:#1f6b3a; --okbg:#12331f;
  --err:#6b2530; --errbg:#33161a; --warn:#8a6d1f; --warnbg:#332b12; }
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin:0; background:var(--bg);
  color:var(--fg); line-height:1.45; }
a { color:var(--accent); }
h1,h2,h3 { margin:0 0 .5em; }
.wrap { max-width: 1000px; margin:0 auto; padding: 18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:18px; margin-bottom:16px; }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.spread { justify-content:space-between; }
.pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:.82rem;
  font-weight:600; border:1px solid var(--line); }
.pill.on { background:var(--okbg); border-color:var(--ok); }
.pill.off { background:#22252b; color:var(--mut); }
.mut { color:var(--mut); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; }
button, .btn { background:var(--accent); color:#fff; border:0; border-radius:8px;
  padding:9px 14px; font-size:.92rem; font-weight:600; cursor:pointer; }
button.sec { background:#2b303a; }
button.danger { background:#b03a4a; }
button:disabled { opacity:.5; cursor:not-allowed; }
input, select { background:#171a20; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:9px 11px; font-size:.92rem; }
input[type=text], input[type=password], input[type=number], input[type=search], select { width:100%; }
label { font-size:.82rem; color:var(--mut); display:block; margin-bottom:4px; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
th { color:var(--mut); font-weight:600; }
.tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
.tab { background:#20242b; color:var(--mut); border:1px solid var(--line); border-radius:8px;
  padding:8px 13px; cursor:pointer; font-weight:600; font-size:.9rem; }
.tab.active { background:var(--accent); color:#fff; border-color:var(--accent); }
.hidden { display:none; }
.note { padding:11px 13px; border-radius:9px; margin:10px 0; font-size:.9rem; }
.note.ok { background:var(--okbg); border:1px solid var(--ok); }
.note.err { background:var(--errbg); border:1px solid var(--err); }
.note.warn { background:var(--warnbg); border:1px solid var(--warn); }
.list { max-height:420px; overflow:auto; border:1px solid var(--line); border-radius:8px; }
.list a, .list .item { display:block; padding:8px 11px; border-bottom:1px solid var(--line);
  text-decoration:none; color:var(--fg); }
.list a:hover { background:#232830; }
.field { margin-bottom:12px; }
code { background:#0e1013; padding:1px 5px; border-radius:5px; font-size:.86em; }
.small { font-size:.82rem; }
"""


def login_page(*, error: str | None = None, attempts_left: int | None = None,
               banned_until: datetime | None = None) -> str:
    if banned_until is not None:
        banner = (f'<div class="note err">Too many failed attempts. This address is '
                  f'blocked until <b>{escape(banned_until.strftime("%Y-%m-%d %H:%M"))}</b>. '
                  f'An admin can lift the block on the host.</div>')
        form = ""
    else:
        banner = ""
        if error:
            extra = ""
            if attempts_left is not None:
                extra = f" {attempts_left} attempt(s) left before this address is blocked."
            banner = f'<div class="note err">{escape(error)}{escape(extra)}</div>'
        form = """
        <form method="post" action="/login">
          <div class="field">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" autofocus required>
          </div>
          <button type="submit">Sign in</button>
        </form>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>acbot — sign in</title><style>{_STYLE}
body {{ display:grid; place-items:center; min-height:100vh; }}
.card {{ width:min(92vw,400px); }}</style></head>
<body><div class="card">
  <h1>Assetto Corsa admin</h1>
  <p class="mut small">Enter the shared admin password.</p>
  {banner}{form}
</div></body></html>"""


def banned_page(banned_until: datetime | None) -> str:
    when = (f"until <b>{escape(banned_until.strftime('%Y-%m-%d %H:%M'))}</b>"
            if banned_until else "indefinitely")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blocked</title><style>{_STYLE}
body {{ display:grid; place-items:center; min-height:100vh; }}</style></head>
<body><div class="card" style="max-width:420px">
  <h1>Access blocked</h1>
  <p>This address is blocked {when} after too many failed sign-ins.</p>
  <p class="mut small">An admin can lift it by editing the ban list on the host.</p>
</div></body></html>"""


def dashboard_page() -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assetto Corsa admin</title><style>{_STYLE}</style></head>
<body><div class="wrap">
  <div class="row spread">
    <h1 id="srvName">Assetto Corsa admin</h1>
    <form method="post" action="/logout" style="margin:0"><button class="sec">Log out</button></form>
  </div>

  <div class="card" id="statusCard">
    <div class="row spread">
      <div><span id="statePill" class="pill off">…</span>
           <span class="mut" id="stateSub"></span></div>
      <button class="sec" onclick="loadStatus()">Refresh</button>
    </div>
    <div id="statusBody" style="margin-top:12px"></div>
  </div>

  <div class="tabs" id="tabs"></div>
  <div id="msg"></div>

  <div class="card tabpanel" data-tab="control">
    <h2>Server control</h2>
    <div class="row">
      <div style="flex:1;min-width:200px">
        <label>Preset to apply on start (optional)</label>
        <select id="startPreset"><option value="">— keep staged —</option></select>
      </div>
    </div>
    <div class="row" style="margin-top:12px">
      <button onclick="serverStart(false)">Start</button>
      <button class="sec" onclick="serverRestart()">Restart</button>
      <button class="danger" onclick="serverStop()">Stop</button>
    </div>
  </div>

  <div class="card tabpanel hidden" data-tab="entries">
    <div class="row spread"><h2>Entry list</h2><button class="sec" onclick="loadEntries()">Reload</button></div>
    <div id="entries"></div>
  </div>

  <div class="card tabpanel hidden" data-tab="presets">
    <div class="row spread"><h2>Presets</h2><button class="sec" onclick="loadPresets()">Reload</button></div>
    <div id="presets"></div>
  </div>

  <div class="card tabpanel hidden" data-tab="content">
    <h2>Content &amp; downloads</h2>
    <div class="field"><input type="search" id="contentSearch" placeholder="Search cars &amp; tracks…"
       oninput="renderContent()"></div>
    <div class="row"><h3 style="flex:1">Cars <span class="mut small" id="carCount"></span></h3></div>
    <div class="list" id="carsList"></div>
    <h3 style="margin-top:14px">Tracks <span class="mut small" id="trackCount"></span></h3>
    <div class="list" id="tracksList"></div>
  </div>

  <div class="card tabpanel hidden" data-tab="settings">
    <h2>Settings <span class="mut small">(staged — apply on restart)</span></h2>
    <div class="row" style="align-items:flex-end">
      <div style="width:150px"><label>Damage %</label><input type="number" id="setDamage" min="0" max="100"></div>
      <button onclick="saveDamage()">Set damage</button>
    </div>
    <div class="row" style="align-items:flex-end;margin-top:12px">
      <div style="width:150px"><label>Time of day (HH:MM)</label><input type="text" id="setTime" placeholder="14:30"></div>
      <button onclick="saveTime()">Set time</button>
    </div>
    <div class="row" style="align-items:flex-end;margin-top:12px">
      <div style="width:150px"><label>Collisions</label>
        <select id="setColl"><option value="on">on</option><option value="off">off</option></select></div>
      <button onclick="saveCollisions()">Set collisions</button>
      <span class="mut small">AssettoServer only</span>
    </div>
  </div>

  <div class="card tabpanel hidden" data-tab="uploads">
    <h2>Car uploads</h2>
    <div id="pending"></div>
    <h3 style="margin-top:14px">Upload a car .zip</h3>
    <form id="uploadForm"><div class="row" style="align-items:flex-end">
      <input type="file" name="file" accept=".zip" required style="flex:1">
      <button type="submit">Upload</button></div></form>
    <p class="mut small">Uploaded cars are held pending — approve here to install into content/cars.</p>
  </div>

  <div class="card tabpanel hidden" data-tab="leaderboard">
    <div class="row spread"><h2>Leaderboard</h2><button class="sec" onclick="loadRecent()">Reload</button></div>
    <h3>Recent laps</h3>
    <div id="recent"></div>
  </div>
</div>

<datalist id="carOptions"></datalist>
<script>{_SCRIPT}</script>
</body></html>"""


# --- client-side app -------------------------------------------------------
_SCRIPT = r"""
const $ = (id) => document.getElementById(id);
let CARS = [], TRACKS = [], DLBASE = "";

async function api(path, method="GET", body=null) {
  const opt = { method, headers: {} };
  if (body !== null) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/login"; return {ok:false, error:"session expired"}; }
  let data = {}; try { data = await r.json(); } catch(e) {}
  data._status = r.status;
  return data;
}

function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function flash(text, cls="ok") {
  // Escapes internally so every caller is safe by default, regardless of
  // whether the text ultimately traces back to server-controlled data.
  const m = $("msg");
  m.innerHTML = '<div class="note '+cls+'">'+esc(text)+'</div>';
  if (cls === "ok") setTimeout(() => { if (m.firstChild) m.innerHTML=""; }, 5000);
}

// -- tabs -------------------------------------------------------------------
const TABS = [["control","Control"],["entries","Entry list"],["presets","Presets"],
  ["content","Content"],["settings","Settings"],["uploads","Uploads"],["leaderboard","Leaderboard"]];
function buildTabs(){
  $("tabs").innerHTML = TABS.map(([k,l],i)=>
    `<div class="tab${i===0?' active':''}" data-t="${k}" onclick="showTab('${k}')">${l}</div>`).join("");
}
function showTab(k){
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active", t.dataset.t===k));
  document.querySelectorAll(".tabpanel").forEach(p=>p.classList.toggle("hidden", p.dataset.tab!==k));
  if (k==="entries") loadEntries();
  if (k==="presets") loadPresets();
  if (k==="content") loadContent();
  if (k==="uploads") loadPending();
  if (k==="leaderboard") loadRecent();
}

// -- status -----------------------------------------------------------------
async function loadStatus(){
  const s = await api("/api/status");
  if (!s.ok) return;
  $("srvName").textContent = s.server_name || "Assetto Corsa admin";
  const pill = $("statePill");
  pill.className = "pill " + (s.running ? "on":"off");
  pill.textContent = s.running ? "Online" : "Offline";
  $("stateSub").textContent = s.backend ? (" · "+s.backend) : "";
  let h = "<table>";
  const add=(k,v)=>{ h += `<tr><th style="width:150px">${k}</th><td>${v}</td></tr>`; };
  add("Preset", esc(s.preset||"none staged"));
  add("Track", esc(s.track||"?"));
  if (s.running){
    add("Uptime", esc(s.uptime||"—"));
    add("Players", esc((s.clients??"?")+" / "+(s.maxclients??"?")));
    if (s.session) add("Session", esc(s.session));
    if (s.drivers && s.drivers.length)
      add("On track", s.drivers.map(d=>esc(d.name)+' <span class="mut">'+esc(d.model)+'</span>').join("<br>"));
    if (s.join_url) add("Join", '<a href="'+esc(s.join_url)+'">Open in Content Manager</a>');
  }
  add("Damage", esc((s.damage??"?")+"%"));
  if (s.time) add("Time of day", esc(s.time));
  h += "</table>";
  $("statusBody").innerHTML = h;
  if (s.damage!=null && !$("setDamage").matches(":focus")) $("setDamage").value = s.damage;
  if (s.time && !$("setTime").matches(":focus")) $("setTime").value = s.time;
}

// -- server control ---------------------------------------------------------
async function serverStart(takeOver){
  const preset = $("startPreset").value;
  const r = await api("/api/server/start","POST",{preset, take_over:takeOver});
  if (r.ok) { flash(r.message||"Started."); loadStatus(); }
  else if (r.code==="stray") {
    if (confirm(r.error+"\n\nKill it and take over?")) serverStart(true);
  } else flash(r.error||"Failed.","err");
}
async function serverStop(){
  if (!confirm("Stop the server? Everyone on track is disconnected.")) return;
  const r = await api("/api/server/stop","POST",{});
  flash(r.ok ? (r.message||"Stopped.") : (r.error||"Failed."), r.ok?"ok":"err"); loadStatus();
}
async function serverRestart(){
  if (!confirm("Restart the server? Everyone on track is disconnected.")) return;
  const r = await api("/api/server/restart","POST",{});
  flash(r.ok ? (r.message||"Restarted.") : (r.error||"Failed."), r.ok?"ok":"err"); loadStatus();
}

// -- entries ----------------------------------------------------------------
async function loadEntries(){
  const r = await api("/api/entries");
  if (!r.ok){ $("entries").innerHTML = '<p class="mut">'+esc(r.error||"unavailable")+'</p>'; return; }
  if (!r.entries.length){ $("entries").innerHTML = '<p class="mut">Entry list is empty (apply a preset).</p>'; return; }
  let h = '<table><tr><th>#</th><th>Car</th><th>Skin</th><th>Driver</th><th></th></tr>';
  for (const e of r.entries){
    h += `<tr>
      <td>${e.slot}</td>
      <td><input list="carOptions" id="car_${e.slot}" value="${esc(e.model)}"></td>
      <td><input id="skin_${e.slot}" value="${esc(e.skin)}" placeholder="default"></td>
      <td class="mut small">${e.driver?esc(e.driver):"—"}</td>
      <td><button class="sec" onclick="saveEntry(${e.slot})">Save</button></td></tr>`;
  }
  h += "</table>";
  $("entries").innerHTML = h;
}
async function saveEntry(slot){
  const car = $("car_"+slot).value.trim();
  const skin = $("skin_"+slot).value.trim();
  const r = await api("/api/entry/setcar","POST",{slot, car, skin});
  flash(r.ok ? (r.message||"Saved.") : (r.error||"Failed."), r.ok?"ok":"err");
  if (r.ok) loadEntries();
}

// -- presets ----------------------------------------------------------------
async function loadPresets(){
  const r = await api("/api/presets");
  const sel = $("startPreset");
  if (r.ok){
    sel.innerHTML = '<option value="">— keep staged —</option>' +
      r.presets.map(p=>`<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  }
  if (!r.ok){ $("presets").innerHTML = '<p class="mut">'+esc(r.error||"No presets found.")+'</p>'; return; }
  if (!r.presets.length){ $("presets").innerHTML = '<p class="mut">No presets found.</p>'; return; }
  let h = '<table><tr><th>Name</th><th>Track</th><th>Slots</th><th></th></tr>';
  for (const p of r.presets){
    const active = p.active ? ' <span class="pill on">active</span>' : '';
    h += `<tr><td><b>${esc(p.name)}</b>${active}</td><td>${esc(p.track||"?")}</td>
      <td>${p.max_clients||"?"}</td>
      <td><button class="sec" onclick="applyPreset('${esc(p.name)}')">Apply</button></td></tr>`;
  }
  h += "</table>";
  $("presets").innerHTML = h;
}
async function applyPreset(name){
  const r = await api("/api/preset/apply","POST",{name});
  flash(r.ok ? (r.message||"Applied.") : (r.error||"Failed."), r.ok?"ok":"err");
  if (r.ok){ loadPresets(); loadStatus(); }
}

// -- content / downloads ----------------------------------------------------
async function loadContent(){
  const r = await api("/api/content");
  if (!r.ok){ $("carsList").innerHTML = '<div class="item mut">'+esc(r.error||"unavailable")+'</div>'; return; }
  CARS = r.cars; TRACKS = r.tracks; DLBASE = r.download_base;
  $("carOptions").innerHTML = CARS.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  renderContent();
}
function renderContent(){
  const q = ($("contentSearch").value||"").toLowerCase();
  const cars = CARS.filter(c => !q || c.id.toLowerCase().includes(q) || (c.name||"").toLowerCase().includes(q));
  const tracks = TRACKS.filter(t => !q || t.toLowerCase().includes(q));
  $("carCount").textContent = "("+cars.length+")";
  $("trackCount").textContent = "("+tracks.length+")";
  $("carsList").innerHTML = cars.length ? cars.map(c=>
    `<a href="${esc(c.url)}"><b>${esc(c.name)}</b> <span class="mut small">${esc(c.id)}</span></a>`).join("")
    : '<div class="item mut">No cars.</div>';
  $("tracksList").innerHTML = tracks.length ? tracks.map(t=>
    `<a href="${esc(DLBASE)}/tracks/${encodeURIComponent(t)}">${esc(t)}</a>`).join("")
    : '<div class="item mut">No tracks.</div>';
}

// -- settings ---------------------------------------------------------------
async function saveDamage(){
  const r = await api("/api/settings/damage","POST",{percent:Number($("setDamage").value)});
  flash(r.ok ? (r.message||"Saved.") : (r.error||"Failed."), r.ok?"ok":"err");
}
async function saveTime(){
  const r = await api("/api/settings/time","POST",{value:$("setTime").value.trim()});
  flash(r.ok ? (r.message||"Saved.") : (r.error||"Failed."), r.ok?"ok":"err");
}
async function saveCollisions(){
  const r = await api("/api/settings/collisions","POST",{state:$("setColl").value});
  flash(r.ok ? (r.message||"Saved.") : (r.error||"Failed."), r.ok?"ok":"err");
}

// -- uploads ----------------------------------------------------------------
async function loadPending(){
  const r = await api("/api/uploads/pending");
  if (!r.ok || !r.pending){ $("pending").innerHTML = '<p class="mut">Nothing awaiting approval.</p>'; return; }
  const p = r.pending;
  $("pending").innerHTML = `<div class="note warn">Awaiting approval: <b>${esc(p.label)}</b>
     <span class="mut small">(${esc(p.filename)})</span><div class="row" style="margin-top:8px">
     <button onclick="approveUpload()">Approve &amp; install</button>
     <button class="danger" onclick="rejectUpload()">Reject</button></div></div>`;
}
async function approveUpload(){
  const r = await api("/api/uploads/approve","POST",{});
  flash(r.ok ? (r.message||"Installed.") : (r.error||"Failed."), r.ok?"ok":"err");
  loadPending(); if (r.ok) loadContent();
}
async function rejectUpload(){
  const r = await api("/api/uploads/reject","POST",{});
  flash(r.ok ? "Rejected." : (r.error||"Failed."), r.ok?"ok":"err"); loadPending();
}
$("uploadForm").addEventListener("submit", async (ev)=>{
  ev.preventDefault();
  const fd = new FormData(ev.target);
  flash("Uploading…","warn");
  const r = await fetch("/api/upload",{method:"POST",body:fd});
  let d={}; try{ d=await r.json(); }catch(e){}
  flash(d.ok ? (d.message||"Uploaded — approve it below.") : (d.error||"Upload failed."), d.ok?"ok":"err");
  ev.target.reset(); loadPending();
});

// -- leaderboard ------------------------------------------------------------
async function loadRecent(){
  const r = await api("/api/leaderboard/recent");
  if (!r.ok || !r.laps.length){ $("recent").innerHTML = '<p class="mut">No laps recorded yet.</p>'; return; }
  let h = '<table><tr><th>Time</th><th>Driver</th><th>Car</th><th>Track</th></tr>';
  for (const l of r.laps)
    h += `<tr><td>${l.clean?"✅":"⚠️"} <b>${esc(l.laptime)}</b></td><td>${esc(l.name)}</td>
      <td>${esc(l.car_model)}</td><td>${esc(l.track)}${l.layout?" ("+esc(l.layout)+")":""}</td></tr>`;
  h += "</table>";
  $("recent").innerHTML = h;
}

// -- boot -------------------------------------------------------------------
buildTabs();
loadStatus();
loadPresets();
setInterval(loadStatus, 5000);
"""
