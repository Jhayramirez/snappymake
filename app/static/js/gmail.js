const $ = (id) => document.getElementById(id);

let POOL = { total: 0, unused: 0, used: 0, emails: [] };
let FILTER = "all";

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

function fmtTime(ts) {
  if (!ts || ts === "0") return "—";
  const n = Number(ts) * (String(ts).length > 11 ? 1 : 1000);
  const d = new Date(n);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString();
}

function setPool(data) {
  POOL = data || { total: 0, unused: 0, used: 0, emails: [] };
}

function renderMeters() {
  $("meters").innerHTML = `
    <article class="meter accent">
      <p class="kicker">Available</p>
      <div class="big">${POOL.unused ?? 0}</div>
      <p>Ready for the next Snapchat signup</p>
    </article>
    <article class="meter">
      <p class="kicker">Used</p>
      <div class="big">${POOL.used ?? 0}</div>
      <p>Already claimed for Snapchat OTP</p>
    </article>
    <article class="meter">
      <p class="kicker">Total</p>
      <div class="big">${POOL.total ?? 0}</div>
      <p>In the local IMAP list</p>
    </article>
  `;
}

function filteredRows() {
  const q = ($("search").value || "").toLowerCase();
  return (POOL.emails || []).filter((row) => {
    if (FILTER === "available" && row.used) return false;
    if (FILTER === "used" && !row.used) return false;
    if (!q) return true;
    const hay = `${row.email} ${row.app_password || ""}`.toLowerCase();
    return hay.includes(q);
  });
}

function attr(v) {
  return encodeURIComponent(v ?? "");
}

function renderTable() {
  const active = document.activeElement;
  const keep = active && active.classList.contains("cell-edit")
    ? { field: active.dataset.field, key: active.closest("tr")?.dataset.key, start: active.selectionStart, end: active.selectionEnd }
    : null;
  const rows = filteredRows();
  $("sheetSub").textContent = `${rows.length} shown · ${POOL.unused} available · ${POOL.used} used`;
  ["filterAll", "filterAvail", "filterUsed"].forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.classList.toggle("solid", btn.dataset.filter === FILTER);
    btn.classList.toggle("ghost", btn.dataset.filter !== FILTER);
  });
  if (!rows.length) {
    $("rows").innerHTML = `<tr><td colspan="7" class="empty">${POOL.total ? "Nothing matches this filter." : "No Gmails yet. Paste email + app password above."}</td></tr>`;
    return;
  }
  $("rows").innerHTML = rows.map((row) => `
    <tr data-key="${esc(row.email)}">
      <td class="edit-cell">
        <input class="cell-edit" data-field="email" spellcheck="false" value="${esc(row.email)}" />
      </td>
      <td class="edit-cell">
        <input class="cell-edit" data-field="app_password" spellcheck="false" autocomplete="off" value="${esc(row.app_password || "")}" />
      </td>
      <td><span class="pill ${row.used ? "off" : "on"}">${row.used ? "used" : "available"}</span></td>
      <td>${esc(row.used ? fmtTime(row.used_at) : "—")}</td>
      <td class="mono">${esc(row.profile_id || "—")}</td>
      <td class="ua-cell">${esc(row.last_error || "—")}</td>
      <td class="row-actions">
        <button data-act="inbox" data-email="${attr(row.email)}">Inbox</button>
        ${row.used ? `<button data-act="release" data-email="${attr(row.email)}">Mark available</button>` : ""}
        <button class="warn" data-act="remove" data-email="${attr(row.email)}">Remove</button>
      </td>
    </tr>
  `).join("");
  if (keep?.key) {
    const el = $("rows").querySelector(`tr[data-key="${CSS.escape(keep.key)}"] [data-field="${keep.field}"]`);
    if (el) {
      el.focus();
      try { el.setSelectionRange(keep.start, keep.end); } catch {}
    }
  }
}

function render() {
  renderMeters();
  renderTable();
}

async function refresh() {
  try {
    setPool(await api("/api/gmail"));
  } catch (err) {
    toast(err.message);
  }
  render();
}

$("btnRefresh").onclick = refresh;
$("search").oninput = renderTable;
["filterAll", "filterAvail", "filterUsed"].forEach((id) => {
  $(id).onclick = () => {
    FILTER = $(id).dataset.filter;
    renderTable();
  };
});

$("gmailForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = e.target.gmail_list.value.trim();
  if (!text) {
    toast("Paste Gmail + app password lines first");
    return;
  }
  try {
    const result = await api("/api/gmail/accounts", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    e.target.gmail_list.value = "";
    setPool(result.pool);
    render();
    const invalid = (result.invalid || []).length;
    toast(`Added ${(result.added || []).length} · skipped ${(result.skipped || []).length}${invalid ? ` · invalid ${invalid}` : ""}`);
  } catch (err) { toast(err.message); }
};

let INBOX = { email: "", folder: "INBOX", messages: [] };

async function openInbox(email) {
  INBOX = { email, folder: "INBOX", messages: [] };
  $("inboxTitle").textContent = email;
  $("inbox").hidden = false;
  $("inboxList").innerHTML = `<p class="muted">Connecting over IMAP…</p>`;
  $("inboxBody").innerHTML = `<p class="muted">Pick a message.</p>`;
  $("inboxHint").textContent = "Newest 30 messages. Nothing is marked read or deleted.";
  try {
    const data = await api(`/api/gmail/folders?account=${encodeURIComponent(email)}`);
    const names = data.folders || ["INBOX"];
    $("inboxFolder").innerHTML = names.map((n) => {
      const sel = n.toUpperCase() === "INBOX" ? " selected" : "";
      return `<option value="${esc(n)}"${sel}>${esc(n)}</option>`;
    }).join("");
    if (!$("inboxFolder").value && names[0]) $("inboxFolder").value = names[0];
    INBOX.folder = $("inboxFolder").value || "INBOX";
  } catch (err) {
    $("inboxFolder").innerHTML = `<option value="INBOX">INBOX</option>`;
    toast(err.message);
  }
  await loadMessages();
}

async function loadMessages() {
  const email = INBOX.email;
  const folder = $("inboxFolder").value || INBOX.folder || "INBOX";
  INBOX.folder = folder;
  $("inboxHint").textContent = `Loading ${folder}…`;
  try {
    const data = await api(`/api/gmail/messages?account=${encodeURIComponent(email)}&folder=${encodeURIComponent(folder)}`);
    INBOX.messages = data.messages || [];
    $("inboxHint").textContent = `${INBOX.messages.length} of ${data.total || 0} in ${folder} · read-only`;
    if (!INBOX.messages.length) {
      $("inboxList").innerHTML = `<p class="muted">This folder is empty.</p>`;
      return;
    }
    $("inboxList").innerHTML = INBOX.messages.map((m) => `
      <button class="inbox-item ${m.snapchat ? "snap" : ""}" data-uid="${esc(m.uid)}">
        <b>${esc(m.from_name || m.from_addr || "unknown")}</b>
        ${m.snapchat ? `<span class="pill on">snapchat</span>` : ""}
        <span>${esc(m.subject)}</span>
        <small>${esc(m.ts ? fmtTime(m.ts) : (m.date || ""))}</small>
      </button>
    `).join("");
  } catch (err) {
    $("inboxList").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    $("inboxHint").textContent = err.message;
  }
}

async function openMessage(uid) {
  $("inboxBody").innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const msg = await api(`/api/gmail/message?account=${encodeURIComponent(INBOX.email)}&folder=${encodeURIComponent(INBOX.folder)}&uid=${encodeURIComponent(uid)}`);
    const frame = msg.body_is_html && msg.html
      ? `<p class="inbox-html-note">HTML rendered in a sandboxed frame (scripts off).</p>
         <iframe class="inbox-html" sandbox referrerpolicy="no-referrer" srcdoc="${esc(msg.html)}"></iframe>`
      : `<pre class="inbox-pre">${esc(msg.body || "(no text)")}</pre>`;
    $("inboxBody").innerHTML = `
      <p class="kicker">${esc(msg.from_addr || msg.from_name || "")}</p>
      <h3>${esc(msg.subject)}</h3>
      ${msg.otp ? `<p class="pill on">OTP ${esc(msg.otp)}</p>` : ""}
      ${frame}
    `;
  } catch (err) {
    $("inboxBody").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

const saveTimers = new Map();

function storedRow(key) {
  const needle = String(key || "").toLowerCase();
  return (POOL.emails || []).find((r) => r.email.toLowerCase() === needle);
}

async function persistRow(tr, { strict = false } = {}) {
  if (!tr) return;
  const key = tr.dataset.key || "";
  const emailInput = tr.querySelector("[data-field=email]");
  const pwInput = tr.querySelector("[data-field=app_password]");
  const newEmail = (emailInput?.value || "").trim();
  const pw = (pwInput?.value || "").replace(/\s+/g, "");
  const current = storedRow(key);
  if (!current) return;
  if (newEmail === current.email && pw === String(current.app_password || "").replace(/\s+/g, "")) {
    return;
  }
  if (!newEmail.includes("@") || !pw) {
    if (strict) {
      tr.querySelectorAll(".cell-edit").forEach((el) => el.classList.add("err"));
      toast("Need a valid email and app password");
    }
    return;
  }
  tr.querySelectorAll(".cell-edit").forEach((el) => {
    el.classList.remove("err", "saved");
    el.classList.add("saving");
  });
  try {
    const result = await api("/api/gmail/accounts", {
      method: "PATCH",
      body: JSON.stringify({ email: key, new_email: newEmail, app_password: pw }),
    });
    setPool(result.pool);
    tr.dataset.key = result.email;
    tr.querySelectorAll("[data-email]").forEach((btn) => {
      btn.dataset.email = encodeURIComponent(result.email);
    });
    if (document.activeElement !== emailInput) emailInput.value = result.email;
    if (document.activeElement !== pwInput) pwInput.value = result.app_password;
    tr.querySelectorAll(".cell-edit").forEach((el) => {
      el.classList.remove("saving", "err");
      el.classList.add("saved");
      setTimeout(() => el.classList.remove("saved"), 900);
    });
  } catch (err) {
    tr.querySelectorAll(".cell-edit").forEach((el) => {
      el.classList.remove("saving");
      el.classList.add("err");
    });
    toast(err.message);
  }
}

function scheduleSave(tr) {
  const key = tr.dataset.key;
  clearTimeout(saveTimers.get(key));
  saveTimers.set(key, setTimeout(() => persistRow(tr), 450));
}

async function flushSave(tr) {
  if (!tr) return;
  const key = tr.dataset.key;
  clearTimeout(saveTimers.get(key));
  saveTimers.delete(key);
  await persistRow(tr, { strict: true });
}

$("rows").addEventListener("input", (e) => {
  const input = e.target.closest(".cell-edit");
  if (!input) return;
  scheduleSave(input.closest("tr"));
});
$("rows").addEventListener("focusout", (e) => {
  const input = e.target.closest(".cell-edit");
  if (!input) return;
  const tr = input.closest("tr");
  if (e.relatedTarget && tr.contains(e.relatedTarget)) return;
  flushSave(tr);
});
$("rows").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const input = e.target.closest(".cell-edit");
  if (!input) return;
  e.preventDefault();
  flushSave(input.closest("tr")).then(() => input.blur());
});

$("rows").onclick = async (e) => {
  const hit = e.target.closest("[data-act]");
  if (!hit) return;
  await flushSave(hit.closest("tr"));
  const email = decodeURIComponent(hit.dataset.email || hit.closest("tr")?.dataset.key || "");
  const act = hit.dataset.act;
  try {
    if (act === "inbox") {
      await openInbox(email);
      return;
    }
    if (act === "release") {
      const result = await api("/api/gmail/accounts/release", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setPool(result.pool);
      toast(`${email} is available again`);
    } else if (act === "remove") {
      if (!confirm(`Remove ${email} from the pool?`)) return;
      const result = await api("/api/gmail/accounts/remove", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setPool(result.pool);
      toast(result.ok ? `Removed ${email}` : "That address was not in the list");
    }
    render();
  } catch (err) { toast(err.message); }
};

$("inboxList").onclick = (e) => {
  const item = e.target.closest("[data-uid]");
  if (!item) return;
  openMessage(item.dataset.uid);
};
$("inboxFolder").onchange = () => loadMessages();
$("btnReloadInbox").onclick = () => loadMessages();
$("btnCloseInbox").onclick = () => { $("inbox").hidden = true; };
$("inbox").addEventListener("click", (e) => {
  if (e.target === $("inbox")) $("inbox").hidden = true;
});

refresh();
