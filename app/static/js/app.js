const $ = (id) => document.getElementById(id);

let STATE = {
  connection: { connected: false, message: "" },
  settings: {},
  group: null,
  profiles: [],
  quota: {},
};

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d)
      ? d.map((x) => x.msg || JSON.stringify(x)).join("; ")
      : (d || data.message || res.statusText);
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function fmtTime(ts) {
  if (!ts || ts === "0") return "never";
  const n = Number(ts) * (String(ts).length > 11 ? 1 : 1000);
  const d = new Date(n);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

function ageInfo(ts) {
  if (!ts || ts === "0") return { text: "unknown", aged24: false, aged48: false, hours: 0 };
  const n = Number(ts) * (String(ts).length > 11 ? 1 : 1000);
  if (Number.isNaN(n)) return { text: "unknown", aged24: false, aged48: false, hours: 0 };
  const diff = Date.now() - n;
  if (diff < 0) return { text: "just now", aged24: false, aged48: false, hours: 0 };
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(hours / 24);
  const remH = hours % 24;
  let text;
  if (hours < 1) text = "<1h";
  else if (hours < 24) text = `${hours}h`;
  else text = remH ? `${days}d ${remH}h` : `${days}d`;
  return { text, aged24: hours >= 24, aged48: hours >= 48, hours };
}

function ageChip(p) {
  const a = ageInfo(p.created_time);
  const color = a.aged48 ? "#22c55e" : a.aged24 ? "#f59e0b" : "#ef4444";
  return `<span class="age-chip" style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:8px;font-size:11px;border:1px solid ${color};color:${color}" title="Age since creation (created ${esc(fmtTime(p.created_time))})">${esc(a.text)}</span>`;
}

function ageDrawer(p) {
  const a = ageInfo(p.created_time);
  const flag = (ok) => (ok ? '<span style="color:#22c55e">24h ✓</span>' : '<span style="color:#ef4444">24h ✗</span>');
  const flag48 = (ok) => (ok ? '<span style="color:#22c55e">48h ✓</span>' : '<span style="color:#ef4444">48h ✗</span>');
  return `${esc(a.text)} · ${flag(a.aged24)} / ${flag48(a.aged48)}`;
}

function bucketCounts() {
  const counts = { new: 0, warm: 0, ready: 0 };
  for (const p of STATE.profiles || []) {
    const b = p.age_bucket || "new";
    if (b in counts) counts[b] += 1;
  }
  return counts;
}

function renderMeters() {
  const q = STATE.quota || {};
  const g = STATE.gmail || {};
  const bc = bucketCounts();
  const remaining = q.unknown_cap ? "∞" : (q.remaining ?? "—");
  const capLine = q.unknown_cap
    ? "Premium cap not set yet — AdsPower will still refuse if you run out"
    : `${q.used} used of ${q.cap}`;
  $("meters").innerHTML = `
    <article class="meter accent">
      <p class="kicker">Profiles left</p>
      <div class="big">${remaining}</div>
      <p>${capLine}</p>
    </article>
    <article class="meter">
      <p class="kicker">SnappyMake group</p>
      <div class="big">${q.group_used ?? 0}</div>
      <p>${STATE.group?.group_name || "SnappyMake"}</p>
    </article>
    <article class="meter">
      <p class="kicker">Account total</p>
      <div class="big">${q.used ?? 0}</div>
      <p>Every AdsPower profile counts against the plan</p>
    </article>
    <article class="meter">
      <p class="kicker">Age buckets</p>
      <div class="big">${bc.new} / ${bc.warm} / ${bc.ready}</div>
      <p>New · 24hrs Pass · 4days Ready</p>
    </article>
    <a class="meter" href="/gmail">
      <p class="kicker">Gmail pool</p>
      <div class="big">${g.unused ?? 0}</div>
      <p>${g.unused ?? 0} available · ${g.used ?? 0} used · open pool</p>
    </a>
    <article class="meter">
      <p class="kicker">Local API</p>
      <div class="big">${STATE.connection.connected ? "ON" : "OFF"}</div>
      <p>${STATE.connection.message || STATE.connection.api_base || ""}</p>
    </article>
  `;
}

let SELECTED = new Set();
let BUCKET_FILTER = "all";

const BUCKET_LABELS = { new: "New", warm: "24hrs Pass", ready: "4days Ready" };

function renderTable() {
  const q = ($("search").value || "").toLowerCase();
  const rows = (STATE.profiles || []).filter((p) => {
    if (BUCKET_FILTER !== "all" && (p.age_bucket || "new") !== BUCKET_FILTER) return false;
    const blob = [p.display_name, p.profile_id, p.proxy_label, p.proxy_type, p.os_name, p.browser, p.kernel, p.user_agent, p.remark, p.platform, p.username, p.ip, p.life]
      .join(" ").toLowerCase();
    return blob.includes(q);
  });
  const live = new Set((STATE.profiles || []).map((p) => p.profile_id));
  SELECTED.forEach((id) => { if (!live.has(id)) SELECTED.delete(id); });
  $("sheetSub").textContent = STATE.connection.connected
    ? `${rows.length} profile${rows.length === 1 ? "" : "s"} in group`
    : STATE.connection.message;
  if (!rows.length) {
    $("rows").innerHTML = `<tr><td colspan="16" class="empty">${STATE.connection.connected ? "No profiles yet. Create a batch." : "Open AdsPower, then Refresh."}</td></tr>`;
    syncSelectUI();
    return;
  }
  $("rows").innerHTML = rows.map((p) => `
    <tr data-id="${p.profile_id}" class="${SELECTED.has(p.profile_id) ? "picked" : ""}">
      <td class="check-cell"><input type="checkbox" data-pick="${p.profile_id}" ${SELECTED.has(p.profile_id) ? "checked" : ""} /></td>
      <td class="name-cell">${esc(p.display_name)}${ageChip(p)}</td>
      <td>${lifeChip(p)}</td>
      <td class="mono">${esc(p.profile_id)}</td>
      <td><span class="dot ${p.browser_open ? "live" : ""}"></span>${p.browser_open ? "open" : "closed"}</td>
      <td>${esc(p.browser || p.kernel || "—")}</td>
      <td>${esc(p.os_name || "—")}</td>
      <td class="ua-cell mono">${esc(p.user_agent || "—")}</td>
      <td>${esc(p.proxy_type || "—")}</td>
      <td>${esc(p.proxy_label)}</td>
      <td class="ua-cell">${esc(p.remark || "—")}</td>
      <td>${p.cookie ? "yes" : "—"}</td>
      <td class="mono">${esc(p.username || "—")}</td>
      <td class="mono">${esc(p.password || "—")}</td>
      <td class="row-actions">
        <button data-act="open">${p.browser_open ? "Reopen" : "Open"}</button>
        <button data-act="close" ${p.browser_open ? "" : "disabled"}>Close</button>
        <button data-act="view">View</button>
        <button class="warn" data-act="delete">Delete</button>
      </td>
    </tr>
  `).join("");
  syncSelectUI();
}

function syncSelectUI() {
  const boxes = [...document.querySelectorAll("#rows input[data-pick]")];
  const visible = boxes.map((box) => box.dataset.pick);
  const n = visible.filter((id) => SELECTED.has(id)).length;
  const all = $("checkAll");
  if (all) {
    all.checked = visible.length > 0 && n === visible.length;
    all.indeterminate = n > 0 && n < visible.length;
    all.disabled = !visible.length;
  }
  const btn = $("btnDeleteSelected");
  if (btn) {
    btn.disabled = SELECTED.size === 0;
    btn.textContent = SELECTED.size ? `Delete ${SELECTED.size}` : "Delete";
  }
}

function renderConn() {
  const pill = $("connPill");
  pill.className = `pill ${STATE.connection.connected ? "on" : "off"}`;
  pill.textContent = STATE.connection.connected ? "AdsPower live" : "AdsPower offline";
}

function renderSettings() {
  const s = STATE.settings || {};
  const form = $("settingsForm");
  form.api_base.value = s.api_base || "";
  form.plan_cap.value = s.plan_cap || 0;
  form.group_name.value = s.group_name || "SnappyMake";
  form.name_prefix.value = s.name_prefix || "SM";
  if (form.otp_provider) form.otp_provider.value = s.otp_provider || "imap";
  if (form.anymessage_token) form.anymessage_token.placeholder = s.anymessage_token ? `set (${s.anymessage_token})` : "leave blank to keep current";
  if (form.anymessage_site) form.anymessage_site.value = s.anymessage_site || "snapchat.com";
  if (form.anymessage_domain) form.anymessage_domain.value = s.anymessage_domain || "mailcom,gmx,hotmail,outlook";
  if (form.diddysms_key) form.diddysms_key.placeholder = s.diddysms_key ? `set (${s.diddysms_key})` : "leave blank to keep current";
  if (form.diddysms_service) form.diddysms_service.value = s.diddysms_service || "snapchat";
  if (form.bucket_warm_hours) form.bucket_warm_hours.value = s.bucket_warm_hours ?? 24;
  if (form.bucket_ready_hours) form.bucket_ready_hours.value = s.bucket_ready_hours ?? 96;
  if (form.auto_move_enabled) form.auto_move_enabled.checked = !!s.auto_move_enabled;
  if (form.sheets_enabled) form.sheets_enabled.checked = !!s.sheets_enabled;
  if (form.sheets_id) form.sheets_id.value = s.sheets_id || "";
  if (form.sheets_creds_path) form.sheets_creds_path.value = s.sheets_creds_path || "secrets/google-sheets.json";
  const st = $("sheetsStatus");
  if (st) st.textContent = s.sheets_enabled ? "sync ON" : "sync OFF";
}

function profileById(id) {
  return (STATE.profiles || []).find((p) => p.profile_id === id);
}

function lifeChip(p, extra = "") {
  const v = String(p.life || "").toLowerCase();
  const tone = v === "live" ? "live" : v === "dead" ? "dead" : "unset";
  return `<select class="life-chip ${tone}" data-life="${escAttr(v || "unset")}" data-id="${escAttr(p.profile_id)}" title="Account life" ${extra}>
    <option value="" ${!v ? "selected" : ""}>—</option>
    <option value="live" ${v === "live" ? "selected" : ""}>Live</option>
    <option value="dead" ${v === "dead" ? "selected" : ""}>Logout/Dead</option>
  </select>`;
}

function paintLifeChip(sel, value) {
  const v = String(value || "").toLowerCase();
  sel.classList.remove("live", "dead", "unset");
  sel.classList.add(v === "live" ? "live" : v === "dead" ? "dead" : "unset");
  sel.dataset.life = v || "unset";
  sel.value = v;
}

async function saveLife(id, value) {
  const prev = profileById(id)?.life || "";
  const p = profileById(id);
  if (p) p.life = value;
  document.querySelectorAll(`select.life-chip[data-id="${CSS.escape(id)}"]`).forEach((sel) => paintLifeChip(sel, value));
  try {
    const data = await api(`/api/profiles/${id}/life`, {
      method: "PATCH",
      body: JSON.stringify({ status: value }),
    });
    if (p) p.life = data.life || value;
  } catch (err) {
    if (p) p.life = prev;
    document.querySelectorAll(`select.life-chip[data-id="${CSS.escape(id)}"]`).forEach((sel) => paintLifeChip(sel, prev));
    toast(err.message);
  }
}

function renderDrawer(p) {
  const fields = p.all_fields || p;
  const extra = Object.entries(fields)
    .filter(([k]) => !["password", "fakey"].includes(k))
    .map(([k, v]) => `<b>${esc(k)}</b><span class="mono">${esc(typeof v === "object" ? JSON.stringify(v) : v ?? "—")}</span>`)
    .join("");
  const fpRows = (p.fingerprint_fields || []).map(
    (row) => `<b>${esc(row.label)}</b><span class="mono">${esc(row.value)}</span>`
  ).join("");
  $("dName").textContent = p.display_name || p.name || p.profile_id;
  $("drawerBody").innerHTML = `
    <div class="row-actions" style="margin-bottom:16px">
      <button class="solid" data-dact="open">Open browser</button>
      <button data-dact="close">Close</button>
      <button data-dact="inject">Open for Playwright</button>
      <button class="warn" data-dact="delete">Delete</button>
    </div>
    <p class="kicker">Fingerprint</p>
    <div class="kv">${fpRows || "<b>—</b><span>No fingerprint cached for this profile yet</span>"}</div>
    <p class="kicker">Network</p>
    <div class="kv">
      <b>Life</b><span>${lifeChip(p, 'data-drawer="1"')}</span>
      <b>Proxy type</b><span>${esc(p.proxy_type || "—")}</span>
      <b>Proxy</b><span>${esc(p.proxy_label)}</span>
      <b>IP</b><span>${esc(p.ip || "—")} ${esc(p.ip_country || "")}</span>
      <b>Remark</b><span>${esc(p.remark || "—")}</span>
      <b>Created</b><span>${esc(fmtTime(p.created_time))}</span>
      <b>Last open</b><span>${esc(fmtTime(p.last_open_time))}</span>
      <b>Age</b><span>${ageDrawer(p)}</span>
    </div>
    <p class="kicker">Assign proxy</p>
    <form id="proxyForm" class="form-grid nested">
      <label class="span-2">Paste one line
        <textarea name="proxy_list" rows="2" placeholder="host:port:user:pass&#10;socks5://user:pass@host:port"></textarea>
      </label>
      <label>Type
        <select name="proxy_type">
          <option value="">Auto-detect</option>
          <option value="http">HTTP</option>
          <option value="https">HTTPS</option>
          <option value="socks5">SOCKS5</option>
        </select>
      </label>
      <div class="form-actions span-2">
        <p class="hint">Closes the browser if it is open, then writes this proxy onto the profile. Current: ${esc(p.proxy_label || "no proxy")}.</p>
        <div class="row-actions">
          <button class="solid" type="submit">Save proxy</button>
          <button type="button" id="btnClearProxy">Clear to no proxy</button>
        </div>
      </div>
    </form>
    <p class="kicker">Credentials — fill after Snapchat inject</p>
    <form id="credForm" class="form-grid nested">
      <label>Name <input name="name" value="${escAttr(p.display_name || "")}" /></label>
      <label>Platform <input name="platform" value="${escAttr(p.platform || "")}" placeholder="snapchat.com" /></label>
      <label>Username <input name="username" value="${escAttr(p.username || "")}" /></label>
      <label>Password <input name="password" value="${escAttr(p.password || "")}" /></label>
      <label class="span-2">Remark <input name="remark" value="${escAttr(p.remark || "")}" /></label>
      <label class="span-2">2FA <input name="fakey" value="${escAttr(p.fakey || "")}" /></label>
      <label class="span-2">Cookies <textarea name="cookie" rows="4">${escAttr(typeof p.cookie === "object" ? JSON.stringify(p.cookie) : (p.cookie || ""))}</textarea></label>
      <div class="form-actions span-2">
        <p class="hint">Playwright signup can write these back via inject/commit.</p>
        <button class="solid" type="submit">Save to AdsPower</button>
      </div>
    </form>
    <p class="kicker">Everything AdsPower returned</p>
    <div class="kv">${extra}</div>
  `;
  $("drawer").hidden = false;
  $("credForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api(`/api/profiles/${p.profile_id}`, {
        method: "PATCH",
        body: JSON.stringify(Object.fromEntries(fd.entries())),
      });
      toast("Credentials saved to AdsPower");
      await refresh();
      const next = profileById(p.profile_id);
      if (next) renderDrawer(next);
    } catch (err) { toast(err.message); }
  };
  $("proxyForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const result = await api(`/api/profiles/${p.profile_id}/proxy`, {
        method: "PATCH",
        body: JSON.stringify({
          proxy_list: fd.get("proxy_list") || "",
          proxy_type: fd.get("proxy_type") || "",
        }),
      });
      toast(`Proxy saved · ${result.geo || result.label || "ok"}`);
      await refresh();
      const next = profileById(p.profile_id);
      if (next) renderDrawer(next);
    } catch (err) { toast(err.message); }
  };
  $("btnClearProxy").onclick = async () => {
    try {
      await api(`/api/profiles/${p.profile_id}/proxy`, {
        method: "PATCH",
        body: JSON.stringify({ clear: true }),
      });
      toast("Cleared to no proxy");
      await refresh();
      const next = profileById(p.profile_id);
      if (next) renderDrawer(next);
    } catch (err) { toast(err.message); }
  };
  $("drawerBody").onclick = async (e) => {
    const act = e.target.dataset.dact;
    if (!act) return;
    try {
      if (act === "open") {
        toast("Opening — AdsPower can take a minute if a kernel is installing");
        await api(`/api/profiles/${p.profile_id}/open`, { method: "POST", body: "{}" });
        toast("Browser opening");
      }
      if (act === "close") {
        await api(`/api/profiles/${p.profile_id}/close`, { method: "POST" });
        toast("Browser closed");
      }
      if (act === "inject") {
        const data = await api(`/api/profiles/${p.profile_id}/inject/start`, { method: "POST" });
        toast("CDP ready — ws copied if available");
        if (data.puppeteer) await navigator.clipboard.writeText(data.puppeteer).catch(() => {});
      }
      if (act === "delete") {
        const result = await deleteProfiles([p.profile_id], p.display_name || p.profile_id);
        if (!result) return;
        $("drawer").hidden = true;
      }
      await refresh();
    } catch (err) { toast(err.message); }
  };
  $("drawerBody").onchange = async (e) => {
    const life = e.target.closest("select.life-chip");
    if (!life) return;
    await saveLife(life.dataset.id, life.value);
  };
}

function esc(v) {
  return String(v ?? "—")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
function escAttr(v) {
  return esc(v).replaceAll('"', "&quot;");
}

let RUN_POLL = null;
let RUN_ID = null;

function kernelChoice(form) {
  const preferred = STATE.kernels?.preferred;
  const raw = form.kernel ? form.kernel.value : "";
  if (preferred) return { kernel: "chrome", kernel_version: preferred };
  if (!raw || raw === "auto") return { kernel: "chrome", kernel_version: "auto" };
  if (raw.includes(":")) {
    const [kernel, version] = raw.split(":");
    return { kernel, kernel_version: version };
  }
  return { kernel: raw, kernel_version: "auto" };
}

function fillKernelSelects(catalog) {
  const preferred = catalog && catalog.preferred;
  const options = (catalog && catalog.options) || [
    { value: "auto", label: "SunBrowser Chrome · latest downloaded" },
  ];
  const latest = preferred && preferred !== "ua_auto" ? `chrome:${preferred}` : "auto";
  ["runForm", "createForm"].forEach((id) => {
    const sel = $(id) && $(id).kernel;
    if (!sel) return;
    sel.innerHTML = options.map((o) => {
      const dis = o.disabled ? " disabled" : "";
      return `<option value="${escAttr(o.value)}"${dis}>${esc(o.label)}</option>`;
    }).join("");
    sel.value = latest;
  });
}

function formPayload(form) {
  const kernel = kernelChoice(form);
  return {
    count: Number(form.count.value),
    name_prefix: form.name_prefix ? form.name_prefix.value : (STATE.settings?.name_prefix || "SM"),
    auto_name: form.auto_name ? form.auto_name.checked : true,
    remark: form.remark ? form.remark.value : "Created by SnappyMake",
    platform: form.platform ? form.platform.value : "snapchat.com",
    username: form.username ? form.username.value : "",
    password: form.password ? form.password.value : "",
    fakey: form.fakey ? form.fakey.value : "",
    auto_username: form.auto_username ? form.auto_username.checked : false,
    auto_password: form.auto_password ? form.auto_password.checked : false,
    username_prefix: form.username_prefix ? form.username_prefix.value : "snap",
    os: form.os ? form.os.value : "win11",
    kernel: kernel.kernel,
    kernel_version: kernel.kernel_version,
    webrtc: form.webrtc ? form.webrtc.value : "disabled",
    fingerprint_mode: form.fingerprint_mode ? form.fingerprint_mode.value : "selective",
    tabs: form.tabs ? form.tabs.value : "",
    cookie: form.cookie ? form.cookie.value : "",
    proxy_mode: form.proxy_mode.value,
    proxy_list: form.proxy_list ? form.proxy_list.value : "",
    proxy_id: form.proxy_id ? form.proxy_id.value : "random",
    proxy_type: form.proxy_type ? form.proxy_type.value : "",
    merge_after_create: form.merge_after_create ? form.merge_after_create.checked : false,
    bitmoji_gender: form.bitmoji_gender ? form.bitmoji_gender.value : "female",
  };
}

function renderRun(run) {
  const pill = $("runStatus");
  if (!run || run.status === "idle") {
    pill.className = "pill off";
    pill.textContent = "idle";
    return;
  }
  const live = ["queued", "running", "cancelling"].includes(run.status);
  pill.className = `pill ${run.status === "done" ? "on" : run.status === "error" ? "off" : "on"}`;
  pill.textContent = `${run.status}${run.requested ? ` · ${run.ok || 0}/${run.requested}` : ""}`;
  const logs = run.logs || [];
  $("runLog").innerHTML = logs.length
    ? logs.map((line) => {
        const cls = line.step === "error" ? "err" : (line.step === "done" ? "ok" : "");
        const t = new Date((line.ts || 0) * 1000).toLocaleTimeString();
        return `<p class="log-line ${cls}"><span class="ts">${esc(t)}</span><span class="step">${esc(line.step)}</span>${esc(line.message)}</p>`;
      }).join("")
    : `<div class="empty">Waiting for the first log line…</div>`;
  $("runLog").scrollTop = $("runLog").scrollHeight;
  if (live && RUN_ID) startRunPoll(RUN_ID);
}

function startRunPoll(id) {
  RUN_ID = id;
  if (RUN_POLL) return;
  RUN_POLL = setInterval(async () => {
    try {
      const run = await api(`/api/runs/${id}`);
      renderRun(run);
      if (["done", "error", "cancelled"].includes(run.status)) {
        clearInterval(RUN_POLL);
        RUN_POLL = null;
        await refresh();
      }
    } catch (err) {
      clearInterval(RUN_POLL);
      RUN_POLL = null;
      toast(err.message);
    }
  }, 700);
}

async function refresh() {
  try {
    STATE = await api("/api/state");
  } catch (err) {
    STATE.connection = { connected: false, message: err.message };
  }
  renderConn();
  renderMeters();
  renderTable();
  renderSettings();
  fillKernelSelects(STATE.kernels);
  if (STATE.run) renderRun(STATE.run);
}

$("btnRefresh").onclick = refresh;
$("search").oninput = renderTable;
$("bucketFilter").onchange = (e) => { BUCKET_FILTER = e.target.value || "all"; renderTable(); };
$("btnSyncGroups").onclick = async () => {
  const btn = $("btnSyncGroups");
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const r = await api("/api/groups/sync", { method: "POST", body: JSON.stringify({ dry_run: false }) });
    const m = r.moved || {};
    const total = (m.new || 0) + (m.warm || 0) + (m.ready || 0);
    toast(total ? `Moved ${total} · New ${m.new || 0} · 24hrs ${m.warm || 0} · Ready ${m.ready || 0}` : "Groups already in sync");
    if (r.errors && r.errors.length) toast(`${r.errors.length} move error(s): ${r.errors[0].error}`);
    await refresh();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
};

async function pushSheets(btn) {
  const prev = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Pushing…"; }
  try {
    const r = await api("/api/sheets/sync", { method: "POST", body: "{}" });
    if (!r.ok) throw new Error(r.detail || "Sheets sync failed");
    toast(`Sheets updated · ${r.profiles || 0} profiles · ${r.proxies || 0} proxies`);
    const st = $("sheetsStatus");
    if (st) st.textContent = `pushed ${r.profiles || 0}/${r.proxies || 0}`;
  } catch (err) {
    toast(err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prev; }
  }
}

$("btnSyncSheets").onclick = () => pushSheets($("btnSyncSheets"));
if ($("btnSheetsSync")) $("btnSheetsSync").onclick = () => pushSheets($("btnSheetsSync"));
if ($("btnSheetsTest")) $("btnSheetsTest").onclick = async () => {
  const btn = $("btnSheetsTest");
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Testing…";
  try {
    const r = await api("/api/sheets/test", { method: "POST", body: "{}" });
    if (!r.ok) throw new Error(r.detail || "Sheets test failed");
    toast(`Sheets OK · ${r.title} · tabs: ${(r.tabs || []).join(", ")}`);
    $("sheetsStatus").textContent = "connected";
  } catch (err) {
    toast(err.message);
    $("sheetsStatus").textContent = "error";
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
};
$("checkAll").onchange = () => {
  const boxes = [...document.querySelectorAll("#rows input[data-pick]")];
  const on = $("checkAll").checked;
  boxes.forEach((box) => {
    if (on) SELECTED.add(box.dataset.pick);
    else SELECTED.delete(box.dataset.pick);
  });
  renderTable();
};
$("rows").onchange = async (e) => {
  const life = e.target.closest("select.life-chip");
  if (life) {
    await saveLife(life.dataset.id, life.value);
    return;
  }
  const box = e.target.closest("input[data-pick]");
  if (!box) return;
  if (box.checked) SELECTED.add(box.dataset.pick);
  else SELECTED.delete(box.dataset.pick);
  const tr = box.closest("tr");
  if (tr) tr.classList.toggle("picked", box.checked);
  syncSelectUI();
};

async function deleteProfiles(ids, label) {
  const names = ids.map((id) => profileById(id)?.display_name || id);
  const who = label || (ids.length === 1 ? names[0] : `${ids.length} profiles`);
  if (!confirm(`Delete ${who} from AdsPower? This cannot be undone.`)) return null;
  const result = await api("/api/profiles/delete", {
    method: "POST",
    body: JSON.stringify({ profile_ids: ids }),
  });
  ids.forEach((id) => SELECTED.delete(id));
  const errNote = result.errors?.length ? ` · ${result.errors.length} failed` : "";
  toast(`Deleted ${result.ok}/${result.requested}${errNote}`);
  await refresh();
  return result;
}

$("btnDeleteSelected").onclick = async () => {
  const ids = [...SELECTED];
  if (!ids.length) return;
  try {
    await deleteProfiles(ids);
  } catch (err) { toast(err.message); }
};
$("btnCreate").onclick = () => {
  $("create").hidden = false;
  const q = STATE.quota || {};
  $("createHint").textContent = (q.unknown_cap
    ? "Plan cap unknown. We'll stop if AdsPower says you're out of slots. "
    : `${q.remaining ?? 0} slot(s) left on the cap you set. `)
    + `New profiles always use Chrome ${STATE.kernels?.preferred || "152"}, slangy usernames, default password, and BLANK extensions.`;
  $("createForm").name_prefix.value = STATE.settings?.name_prefix || "SM";
};
$("btnRun").onclick = () => document.querySelector(".run-sheet").scrollIntoView({ behavior: "smooth" });
async function loadAmBalance() {
  const el = $("amBalance");
  if (!el) return;
  el.textContent = "checking…";
  try {
    const data = await api("/api/anymessage/balance");
    el.textContent = data.ok ? `$${Number(data.balance).toFixed(2)}` : (data.detail || "unavailable");
  } catch (err) {
    el.textContent = "error: " + err.message;
  }
}
async function loadDiddyBalance() {
  const el = $("diddyBalance");
  if (!el) return;
  el.textContent = "checking…";
  try {
    const data = await api("/api/diddysms/balance");
    if (!data.ok) {
      el.textContent = data.detail || "unavailable";
      return;
    }
    const price = data.price != null ? ` · ${data.service} $${Number(data.price).toFixed(2)}` : "";
    const stock = data.stock != null ? ` · stock ${data.stock}` : "";
    el.textContent = `$${Number(data.balance).toFixed(2)}${price}${stock}`;
  } catch (err) {
    el.textContent = "error: " + err.message;
  }
}
$("btnSettings").onclick = () => { $("settings").hidden = false; loadAmBalance(); loadDiddyBalance(); };
$("btnAmBalance").onclick = loadAmBalance;
if ($("btnDiddyBalance")) $("btnDiddyBalance").onclick = loadDiddyBalance;
$("btnCloseDrawer").onclick = () => { $("drawer").hidden = true; };
$("btnCloseCreate").onclick = () => { $("create").hidden = true; };
$("btnCloseSettings").onclick = () => { $("settings").hidden = true; };
$("btnCloseProxyPool").onclick = () => { $("proxyPool").hidden = true; };
["drawer", "create", "settings", "proxyPool"].forEach((id) => {
  $(id).addEventListener("click", (e) => {
    if (e.target === $(id)) $(id).hidden = true;
  });
});

// ---- Proxy Pool Manager ----
function renderProxyPoolSummary(pool) {
  const cap = pool?.cap ?? 3;
  const failLimit = pool?.fail_limit ?? 5;
  const total = pool?.total ?? 0;
  const avail = pool?.available ?? 0;
  const full = pool?.full ?? 0;
  const resting = pool?.resting ?? 0;
  const rotation = pool?.rotation === "fill" ? "fill" : "spread";
  const summary = $("proxyPoolSummary");
  if (summary) summary.textContent = `${total} prox${total === 1 ? "y" : "ies"} · ${avail} available · ${full} full · ${resting} resting · cap ${cap} · rest@${failLimit} · ${rotation}`;
  const hint = $("proxyPoolHint");
  if (hint) {
    hint.textContent = total
      ? `Proxy pool · ${avail} available of ${total}${resting ? ` · ${resting} resting` : ""} · cap ${cap} · rest after ${failLimit} fails.`
      : "Proxy pool is empty — click Manage Proxy to add some.";
  }
}

function proxyStatusChip(row) {
  const tone = row.status === "available" ? "on" : "off";
  const label = row.disabled ? "disabled" : row.status;
  const extra = row.status === "resting" ? ' style="display:inline-block;border-color:#f59e0b;color:#f59e0b"' : ' style="display:inline-block"';
  return `<span class="pill ${tone}"${extra}>${esc(label)}</span>`;
}

function renderProxyPool(pool) {
  const capInput = $("proxyCapForm") && $("proxyCapForm").proxy_cap;
  if (capInput) capInput.value = pool?.cap ?? 3;
  const failInput = $("proxyCapForm") && $("proxyCapForm").proxy_fail_limit;
  if (failInput) failInput.value = pool?.fail_limit ?? 5;
  const rotationInput = $("proxyCapForm") && $("proxyCapForm").proxy_rotation;
  if (rotationInput) rotationInput.value = pool?.rotation === "fill" ? "fill" : "spread";
  renderProxyPoolSummary(pool);
  const rows = pool?.proxies || [];
  const body = $("proxyPoolRows");
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty">No proxies yet. Add a block above.</td></tr>`;
    return;
  }
  const limit = pool?.fail_limit ?? 5;
  body.innerHTML = rows.map((r) => `
    <tr>
      <td class="mono">${esc(r.label || r.key)}</td>
      <td>${esc(r.geo || "—")}</td>
      <td>${esc(r.using ?? 0)}</td>
      <td>${esc(r.success_count ?? 0)}</td>
      <td>${esc(r.remaining ?? 0)}</td>
      <td>${esc(r.fail_streak ?? 0)}/${esc(limit)}</td>
      <td>${proxyStatusChip(r)}</td>
      <td class="row-actions">${r.status === "resting" ? `<button data-proxy-wake="${escAttr(r.key)}">Wake</button>` : ""}<button class="warn" data-proxy-remove="${escAttr(r.key)}">Remove</button></td>
    </tr>
  `).join("");
}

async function loadProxyPool() {
  try {
    const pool = await api("/api/proxy/pool");
    renderProxyPool(pool);
  } catch (err) {
    toast(err.message);
  }
}

$("btnManageProxy").onclick = () => { $("proxyPool").hidden = false; loadProxyPool(); };

$("proxyCapForm").onsubmit = async (e) => {
  e.preventDefault();
  const cap = Number(e.target.proxy_cap.value || 3);
  const failLimit = Number(e.target.proxy_fail_limit.value || 5);
  const rotation = e.target.proxy_rotation ? e.target.proxy_rotation.value : "spread";
  try {
    const data = await api("/api/proxy/pool/cap", {
      method: "PUT",
      body: JSON.stringify({ cap, fail_limit: failLimit, rotation }),
    });
    toast(`Saved · cap ${data.cap} · rest after ${data.fail_limit} fails · ${data.rotation === "fill" ? "fill" : "spread"}`);
    await loadProxyPool();
  } catch (err) { toast(err.message); }
};

$("proxyAddForm").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const list = (form.proxy_list.value || "").trim();
  if (!list) { toast("Paste at least one proxy first"); return; }
  try {
    const data = await api("/api/proxy/pool", {
      method: "POST",
      body: JSON.stringify({
        proxy_list: list,
        proxy_type: form.proxy_type ? form.proxy_type.value : "",
        test: form.test ? form.test.checked : false,
      }),
    });
    const added = (data.added || []).length;
    const skipped = (data.skipped || []).length;
    const invalid = (data.invalid || []).length;
    toast(`Added ${added} · skipped ${skipped}${invalid ? ` · ${invalid} invalid` : ""}`);
    form.proxy_list.value = "";
    renderProxyPool(data.pool);
  } catch (err) { toast(err.message); }
};

$("proxyPoolRows").onclick = async (e) => {
  const wakeBtn = e.target.closest("button[data-proxy-wake]");
  if (wakeBtn) {
    const key = wakeBtn.dataset.proxyWake;
    try {
      const data = await api("/api/proxy/pool/wake", {
        method: "POST",
        body: JSON.stringify({ key }),
      });
      toast("Proxy woken — back in rotation");
      renderProxyPool(data.pool);
    } catch (err) { toast(err.message); }
    return;
  }
  const btn = e.target.closest("button[data-proxy-remove]");
  if (!btn) return;
  const key = btn.dataset.proxyRemove;
  if (!confirm(`Remove ${key} from the pool? Existing profiles keep their proxy.`)) return;
  try {
    const data = await api("/api/proxy/pool/remove", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
    toast("Proxy removed from pool");
    renderProxyPool(data.pool);
  } catch (err) { toast(err.message); }
};

function syncRunProxyMode() {
  const form = $("runForm");
  if (!form || !form.proxy_mode) return;
  const mode = form.proxy_mode.value;
  const listWrap = $("runProxyListWrap");
  if (listWrap) listWrap.hidden = mode !== "list";
  const hint = $("proxyPoolHint");
  if (hint && mode !== "pool") {
    hint.textContent = mode === "list"
      ? "Paste-list mode — proxies below are rotated round-robin (Manage Proxy unused)."
      : mode === "saved"
        ? "Saved-ID mode — AdsPower assigns the proxy by ID."
        : "No proxy — profiles run on this machine's IP.";
  } else if (hint && mode === "pool") {
    loadProxyPool();
  }
}
if ($("runForm") && $("runForm").proxy_mode) {
  $("runForm").proxy_mode.addEventListener("change", syncRunProxyMode);
}

$("rows").onclick = async (e) => {
  const btn = e.target.closest("button");
  const tr = e.target.closest("tr");
  if (!btn || !tr) return;
  const id = tr.dataset.id;
  const p = profileById(id);
  try {
    if (btn.dataset.act === "view") { renderDrawer(p); return; }
    if (btn.dataset.act === "open") {
      toast("Opening — AdsPower can take a minute if a kernel is installing");
      await api(`/api/profiles/${id}/open`, { method: "POST", body: "{}" });
      toast(`Opened ${p?.display_name || id}`);
    }
    if (btn.dataset.act === "close") {
      await api(`/api/profiles/${id}/close`, { method: "POST" });
      toast(`Closed ${p?.display_name || id}`);
    }
    if (btn.dataset.act === "delete") {
      await deleteProfiles([id], p?.display_name || id);
      return;
    }
    await refresh();
  } catch (err) { toast(err.message); }
};

$("createForm").onsubmit = async (e) => {
  e.preventDefault();
  const payload = formPayload(e.target);
  try {
    const result = await api("/api/profiles", { method: "POST", body: JSON.stringify(payload) });
    const errNote = result.errors?.length ? ` · ${result.errors.length} failed` : "";
    toast(`Created ${result.ok}/${result.requested}${errNote}`);
    $("create").hidden = true;
    await refresh();
  } catch (err) { toast(err.message); }
};

$("runForm").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const payload = {
    ...formPayload(form),
    action: form.run_action.value,
    start_url: form.start_url.value,
    close_after: form.close_after.checked,
    dwell_seconds: Number(form.dwell_seconds.value || 6),
    platform: "snapchat.com",
  };
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
    toast("Run started");
    renderRun(run);
    startRunPoll(run.id);
  } catch (err) { toast(err.message); }
};

$("btnCancelRun").onclick = async () => {
  if (!RUN_ID) return;
  try {
    await api(`/api/runs/${RUN_ID}/cancel`, { method: "POST" });
    toast("Cancel requested");
  } catch (err) { toast(err.message); }
};

async function testProxyForm(form) {
  const list = form.proxy_list ? form.proxy_list.value.trim() : "";
  if (!list) {
    toast("Paste at least one proxy first");
    return;
  }
  toast("Testing proxies…");
  try {
    const result = await api("/api/proxy/test", {
      method: "POST",
      body: JSON.stringify({
        proxy_list: list,
        proxy_type: form.proxy_type ? form.proxy_type.value : "",
      }),
    });
    const lines = (result.tests || []).map((row) => `${row.ok ? "OK" : "FAIL"} ${row.label} ${row.message}`);
    const parseFails = (result.parse_errors || []).map((row) => `PARSE line ${row.line}: ${row.error}`);
    toast(`Proxies ${result.passed || 0} passed · ${result.failed || 0} failed`);
    if ($("runLog") && form.id === "runForm") {
      $("runLog").innerHTML = [...parseFails, ...lines].map((msg) => `<p class="log-line">${esc(msg)}</p>`).join("") || $("runLog").innerHTML;
    }
  } catch (err) { toast(err.message); }
}

$("btnTestRunProxy").onclick = () => testProxyForm($("runForm"));
$("btnTestCreateProxy").onclick = () => testProxyForm($("createForm"));

$("settingsForm").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        api_base: form.api_base.value,
        ...(form.api_key.value.trim() ? { api_key: form.api_key.value.trim() } : {}),
        plan_cap: Number(form.plan_cap.value || 0),
        group_name: form.group_name.value,
        name_prefix: form.name_prefix.value,
        otp_provider: form.otp_provider ? form.otp_provider.value : "imap",
        ...(form.anymessage_token && form.anymessage_token.value.trim() ? { anymessage_token: form.anymessage_token.value.trim() } : {}),
        ...(form.anymessage_site ? { anymessage_site: form.anymessage_site.value } : {}),
        ...(form.anymessage_domain ? { anymessage_domain: form.anymessage_domain.value } : {}),
        ...(form.diddysms_key && form.diddysms_key.value.trim() ? { diddysms_key: form.diddysms_key.value.trim() } : {}),
        ...(form.diddysms_service ? { diddysms_service: form.diddysms_service.value.trim() || "snapchat" } : {}),
        ...(form.bucket_warm_hours ? { bucket_warm_hours: Number(form.bucket_warm_hours.value || 24) } : {}),
        ...(form.bucket_ready_hours ? { bucket_ready_hours: Number(form.bucket_ready_hours.value || 96) } : {}),
        ...(form.auto_move_enabled ? { auto_move_enabled: form.auto_move_enabled.checked } : {}),
        ...(form.sheets_enabled ? { sheets_enabled: form.sheets_enabled.checked } : {}),
        ...(form.sheets_id ? { sheets_id: form.sheets_id.value.trim() } : {}),
        ...(form.sheets_creds_path ? { sheets_creds_path: form.sheets_creds_path.value.trim() } : {}),
      }),
    });
    toast("Settings saved");
    $("settings").hidden = true;
    await refresh();
  } catch (err) { toast(err.message); }
};

refresh();
syncRunProxyMode();
