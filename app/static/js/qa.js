const $ = (id) => document.getElementById(id);

let STATE = { profiles: [], snaps21: {}, run: null };
let BUCKET = "all";
let POLL = null;

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  return data;
}

function availChip(p) {
  if (p.available) return `<span class="pill live">ready</span>`;
  const reason = esc(p.snap_reason || p.local_reason || "unavailable");
  return `<span class="pill off" title="${reason}">blocked</span>`;
}

function snapChip(p) {
  const snap = p.snap || {};
  if (p.snap_ok) {
    const rem = snap.usage && snap.usage.remaining != null ? ` · ${snap.usage.remaining} left` : "";
    return `<span class="hint" title="${esc(snap.message || "")}">OK${esc(rem)}</span>`;
  }
  const wait = snap.wait_seconds ? ` · ${Math.ceil(snap.wait_seconds / 60)}m` : "";
  return `<span class="hint" title="${esc(p.snap_reason || "")}">wait${esc(wait)}</span>`;
}

function renderMeters() {
  const total = STATE.profiles.length;
  const avail = STATE.profiles.filter((p) => p.available).length;
  const base = STATE.snaps21?.base || "—";
  const on = STATE.snaps21?.enabled ? "ON" : "OFF";
  $("meters").innerHTML = `
    <div class="meter"><b>${total}</b><span>profiles</span></div>
    <div class="meter"><b>${avail}</b><span>available</span></div>
    <div class="meter"><b>${esc(on)}</b><span>SnapX</span></div>
    <div class="meter mono"><b style="font-size:12px">${esc(base)}</b><span>base</span></div>
  `;
}

function renderTable() {
  const q = ($("search").value || "").toLowerCase();
  const rows = STATE.profiles.filter((p) => {
    const blob = [p.name, p.profile_id, p.username, p.used_by, p.bucket, p.life]
      .join(" ").toLowerCase();
    return blob.includes(q);
  });
  $("sheetSub").textContent = `${rows.length} shown · ${STATE.profiles.filter((p) => p.available).length} available · bucket ${BUCKET}`;
  if (!rows.length) {
    $("rows").innerHTML = `<tr><td colspan="9" class="empty">No profiles in this filter.</td></tr>`;
    return;
  }
  $("rows").innerHTML = rows.map((p) => `
    <tr data-id="${esc(p.profile_id)}">
      <td class="mono">${esc(p.profile_no || "—")}</td>
      <td>${esc(p.name || "—")}</td>
      <td class="mono">${esc(p.username || p.used_by || "—")}</td>
      <td>${esc(p.bucket || "—")}</td>
      <td>${esc(p.life === "dead" || p.life === "logout" ? "Logout/Dead" : (p.life || "—"))}</td>
      <td>${p.browser_open ? "open" : "closed"}</td>
      <td>${snapChip(p)}</td>
      <td>${availChip(p)}</td>
      <td class="row-actions">
        <button data-qa="${esc(p.profile_id)}" ${STATE.run && ["queued","running"].includes(STATE.run.status) ? "disabled" : ""}>Run QA</button>
      </td>
    </tr>
  `).join("");
}

function renderRun() {
  const run = STATE.run;
  const pill = $("runStatus");
  if (!run) {
    pill.className = "pill off";
    pill.textContent = "idle";
    $("btnCancel").disabled = true;
    $("runSub").textContent = "Check availability per profile, then open + add SnapX usernames.";
    return;
  }
  const busy = ["queued", "running"].includes(run.status);
  pill.className = `pill ${run.status === "done" ? "live" : run.status === "error" ? "off" : ""}`;
  pill.textContent = `${run.status} · ok ${run.ok || 0} / skip ${run.skipped || 0} / fail ${run.failed || 0}`;
  $("btnCancel").disabled = !busy;
  $("btnRunAll").disabled = busy;
  $("runSub").textContent = `Run ${run.id} · claim ${run.count} · bucket ${run.bucket || "all"}`;
  const logs = (run.logs || []).slice(-40).reverse();
  $("runLog").innerHTML = logs.map((l) =>
    `<p class="log-line"><b>${esc(l.kind)}</b> ${esc(l.message)}</p>`
  ).join("") || `<p class="hint">No logs yet</p>`;
}

async function refresh() {
  const bucket = $("bucketFilter").value || "all";
  BUCKET = bucket;
  try {
    const data = await api(`/api/qa/profiles?bucket=${encodeURIComponent(bucket)}`);
    STATE.profiles = (data.profiles || []).map((p) => ({
      ...p,
      // profile_no may only live on full dashboard rows — keep blank if missing
      profile_no: p.profile_no || "",
    }));
    STATE.snaps21 = data.snaps21 || {};
    const form = $("snapsForm");
    if (form) {
      form.enabled.checked = !!STATE.snaps21.enabled;
      form.base.value = STATE.snaps21.base || "https://21snaps.laravel.cloud";
    }
    $("connPill").className = "pill live";
    $("connPill").textContent = "ready";
    $("snapsHint").textContent = STATE.snaps21.enabled ? "SnapX ON" : "SnapX OFF";
    renderMeters();
    renderTable();
  } catch (err) {
    $("connPill").className = "pill off";
    $("connPill").textContent = "error";
    toast(err.message);
  }
}

async function pollRun(runId) {
  clearInterval(POLL);
  const tick = async () => {
    try {
      const data = await api(`/api/qa/run/${runId}`);
      STATE.run = data.run;
      renderRun();
      renderTable();
      if (!STATE.run || !["queued", "running"].includes(STATE.run.status)) {
        clearInterval(POLL);
        POLL = null;
        await refresh();
      }
    } catch (err) {
      clearInterval(POLL);
      POLL = null;
      toast(err.message);
    }
  };
  await tick();
  POLL = setInterval(tick, 1500);
}

async function startRun(profileIds) {
  const count = Number($("snapsForm").count.value || 1);
  const onlyAvail = $("onlyAvail").checked;
  const bucket = $("bucketFilter").value || "all";
  try {
    const data = await api("/api/qa/run", {
      method: "POST",
      body: JSON.stringify({
        profile_ids: profileIds || null,
        bucket: profileIds ? null : (bucket === "all" ? null : bucket),
        count,
        close_after: true,
        only_available: profileIds ? false : onlyAvail,
      }),
    });
    toast(profileIds ? "Run QA started" : "Run ALL started");
    await pollRun(data.run_id);
  } catch (err) {
    toast(err.message);
  }
}

$("btnRefresh").onclick = refresh;
$("search").oninput = renderTable;
$("bucketFilter").onchange = refresh;

$("snapsForm").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  try {
    const data = await api("/api/qa/snaps21", {
      method: "PUT",
      body: JSON.stringify({
        enabled: form.enabled.checked,
        base: form.base.value.trim(),
      }),
    });
    STATE.snaps21 = data;
    $("snapsHint").textContent = data.enabled ? "SnapX ON" : "SnapX OFF";
    toast("SnapX settings saved");
    await refresh();
  } catch (err) {
    toast(err.message);
  }
};

$("btnPing").onclick = async () => {
  try {
    const data = await api("/api/qa/snaps21");
    const ping = data.ping || {};
    if (ping.ok) toast(`Connected · ${data.base}`);
    else toast(ping.detail || "Ping failed");
    $("snapsHint").textContent = ping.ok ? "connected" : "unreachable";
  } catch (err) {
    toast(err.message);
  }
};

$("btnRunAll").onclick = () => startRun(null);

$("btnCancel").onclick = async () => {
  if (!STATE.run?.id) return;
  try {
    await api(`/api/qa/run/${STATE.run.id}/cancel`, { method: "POST", body: "{}" });
    toast("Cancel requested");
  } catch (err) {
    toast(err.message);
  }
};

$("rows").onclick = (e) => {
  const btn = e.target.closest("button[data-qa]");
  if (!btn) return;
  startRun([btn.dataset.qa]);
};

refresh();
