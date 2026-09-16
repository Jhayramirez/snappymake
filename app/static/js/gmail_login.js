const $ = (id) => document.getElementById(id);

let POOL = { total: 0, logged_in: 0, pending: 0, signed_up: 0, codes_total: 0, codes_used: 0, codes_left: 0, accounts: [] };
let FILTER = "all";

const LOGIN_STATES = ["pending", "login_ok", "captcha", "selfie", "error"];
const SNAP_STATES = ["none", "signed_up", "failed"];
const LOGIN_LABEL = { pending: "needs login", login_ok: "logged in", captcha: "captcha", selfie: "selfie", error: "error" };
const SNAP_LABEL = { none: "—", signed_up: "signed up", failed: "failed" };

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

function esc(v) {
  return String(v ?? "—")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function attr(v) {
  return encodeURIComponent(v ?? "");
}

function fmtCode(code) {
  const s = String(code || "");
  if (s.length === 8) return `${s.slice(0, 4)} ${s.slice(4)}`;
  return s;
}

function setPool(data) {
  POOL = data || POOL;
}

function renderMeters() {
  $("meters").innerHTML = `
    <article class="meter accent">
      <p class="kicker">Logged in</p>
      <div class="big">${POOL.logged_in ?? 0}</div>
      <p>Gmail login OK</p>
    </article>
    <article class="meter">
      <p class="kicker">Needs login</p>
      <div class="big">${POOL.pending ?? 0}</div>
      <p>Not yet warmed</p>
    </article>
    <article class="meter">
      <p class="kicker">Accounts</p>
      <div class="big">${POOL.total ?? 0}</div>
      <p>In the login pool</p>
    </article>
    <article class="meter">
      <p class="kicker">Codes left</p>
      <div class="big">${POOL.codes_left ?? 0}</div>
      <p>${POOL.codes_used ?? 0} used of ${POOL.codes_total ?? 0}</p>
    </article>
    <article class="meter">
      <p class="kicker">Snap signed up</p>
      <div class="big">${POOL.signed_up ?? 0}</div>
      <p>via Google</p>
    </article>
  `;
}

function matchesFilter(row) {
  if (FILTER === "all") return true;
  if (FILTER === "issue") return ["captcha", "selfie", "error"].includes(row.login_status);
  return row.login_status === FILTER;
}

function filteredRows() {
  const q = ($("search").value || "").toLowerCase();
  return (POOL.accounts || []).filter((row) => {
    if (!matchesFilter(row)) return false;
    if (!q) return true;
    return row.email.toLowerCase().includes(q);
  });
}

function selectHtml(field, value, states, labels) {
  const opts = states.map((s) =>
    `<option value="${s}"${s === value ? " selected" : ""}>${esc(labels[s] || s)}</option>`
  ).join("");
  return `<select class="status-select" data-field="${field}">${opts}</select>`;
}

function codeChips(row) {
  if (!row.codes || !row.codes.length) return `<span class="muted">no codes</span>`;
  return `<div class="code-chips">` + row.codes.map((c) =>
    `<span class="code-chip ${c.used ? "used" : ""}" data-ord="${c.ordinal}" title="${c.used ? "used — click to mark unused" : "unused — click to mark used"}">${esc(fmtCode(c.code))}</span>`
  ).join("") + `</div>`;
}

function renderTable() {
  const rows = filteredRows();
  $("sheetSub").textContent = `${rows.length} shown · ${POOL.logged_in} logged in · ${POOL.codes_left} codes left`;
  ["filterAll", "filterPending", "filterOk", "filterIssue"].forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.classList.toggle("solid", btn.dataset.filter === FILTER);
    btn.classList.toggle("ghost", btn.dataset.filter !== FILTER);
  });
  if (!rows.length) {
    $("rows").innerHTML = `<tr><td colspan="8" class="empty">${POOL.total ? "Nothing matches this filter." : "No accounts yet. Paste email + password + backup codes above."}</td></tr>`;
    return;
  }
  $("rows").innerHTML = rows.map((row) => `
    <tr data-key="${esc(row.email)}">
      <td class="mono">${esc(row.email)}</td>
      <td class="mono">${esc(row.password || "—")}</td>
      <td>${selectHtml("login_status", row.login_status, LOGIN_STATES, LOGIN_LABEL)}</td>
      <td>${selectHtml("snap_status", row.snap_status, SNAP_STATES, SNAP_LABEL)}</td>
      <td class="mono">${esc(row.profile_id || "—")}</td>
      <td>${esc(row.proxy_label || "—")}</td>
      <td>${codeChips(row)}</td>
      <td class="row-actions">
        <button class="warn" data-act="remove" data-email="${attr(row.email)}">Remove</button>
      </td>
    </tr>
  `).join("");
}

function render() {
  renderMeters();
  renderTable();
}

async function refresh() {
  try {
    setPool(await api("/api/gmail-login"));
  } catch (err) {
    toast(err.message);
  }
  render();
}

$("btnRefresh").onclick = refresh;
$("search").oninput = renderTable;
["filterAll", "filterPending", "filterOk", "filterIssue"].forEach((id) => {
  $(id).onclick = () => { FILTER = $(id).dataset.filter; renderTable(); };
});

$("importForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = e.target.login_list.value.trim();
  if (!text) { toast("Paste email + password + backup code lines first"); return; }
  try {
    const result = await api("/api/gmail-login/import", {
      method: "POST",
      body: JSON.stringify({ text, exclude_used_group: $("excludeUsed").checked }),
    });
    e.target.login_list.value = "";
    setPool(result.pool);
    render();
    const parts = [
      `added ${(result.added || []).length}`,
      `updated ${(result.updated || []).length}`,
    ];
    if ((result.excluded || []).length) parts.push(`excluded ${(result.excluded).length}`);
    if ((result.invalid || []).length) parts.push(`invalid ${(result.invalid).length}`);
    if (result.exclude_error) parts.push("exclusion skipped (AdsPower offline)");
    toast(parts.join(" · "));
  } catch (err) { toast(err.message); }
};

$("btnOfficialGroup").onclick = async () => {
  try {
    const r = await api("/api/gmail-login/official-group", { method: "POST" });
    toast(r.created ? `Created "${r.name}" (id ${r.group_id})` : `"${r.name}" already exists (id ${r.group_id})`);
  } catch (err) { toast(err.message); }
};

$("rows").addEventListener("change", async (e) => {
  const sel = e.target.closest(".status-select");
  if (!sel) return;
  const email = sel.closest("tr")?.dataset.key;
  const field = sel.dataset.field;
  try {
    const r = await api("/api/gmail-login/status", {
      method: "POST",
      body: JSON.stringify({ email, [field]: sel.value }),
    });
    setPool(r.pool);
    render();
  } catch (err) { toast(err.message); refresh(); }
});

$("rows").addEventListener("click", async (e) => {
  const chip = e.target.closest(".code-chip");
  if (chip) {
    const email = chip.closest("tr")?.dataset.key;
    const ordinal = Number(chip.dataset.ord);
    const used = !chip.classList.contains("used");
    try {
      const r = await api("/api/gmail-login/code", {
        method: "POST",
        body: JSON.stringify({ email, ordinal, used }),
      });
      setPool(r.pool);
      render();
    } catch (err) { toast(err.message); }
    return;
  }
  const hit = e.target.closest("[data-act]");
  if (!hit) return;
  const email = decodeURIComponent(hit.dataset.email || "");
  if (hit.dataset.act === "remove") {
    if (!confirm(`Remove ${email} from the login pool?`)) return;
    try {
      const r = await api("/api/gmail-login/remove", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setPool(r.pool);
      render();
      toast(r.ok ? `Removed ${email}` : "Not found");
    } catch (err) { toast(err.message); }
  }
});

refresh();
