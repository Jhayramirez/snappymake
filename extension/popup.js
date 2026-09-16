"use strict";

const DEFAULT_BASE = "http://127.0.0.1:8799";
const DEFAULT_AM_TOKEN = "7ew8qIG7h1F9GuRahID2N5z5GOSonEhI";
const DEFAULT_AM_SITE = "snapchat.com";
const DEFAULT_PROVIDER = "anymessage";

const $ = (id) => document.getElementById(id);
const els = {
  settings: $("settings"),
  settingsToggle: $("settings-toggle"),
  baseUrl: $("base-url"),
  saveBase: $("save-base"),
  provider: $("provider"),
  amToken: $("am-token"),
  amSite: $("am-site"),
  saveAm: $("save-am"),
  checkBalance: $("check-balance"),
  amBalance: $("am-balance"),
  manualEmail: $("manual-email"),
  manualPass: $("manual-pass"),
  saveManual: $("save-manual"),
  clearManual: $("clear-manual"),
  manualSaved: $("manual-saved"),
  refresh: $("refresh"),
  autoBox: $("auto-box"),
  autoEmail: $("auto-email"),
  autoNote: $("auto-note"),
  manualBox: $("manual-box"),
  manualEmailInput: $("manual-email-input"),
  manualPassInput: $("manual-pass-input"),
  fetchManual: $("fetch-manual"),
  contextHint: $("context-hint"),
  status: $("status"),
  linkValue: $("link-value"),
  linkBadge: $("link-badge"),
  openLink: $("open-link"),
  copyLink: $("copy-link"),
  reorderLink: $("reorder-link"),
  otpValue: $("otp-value"),
  otpBadge: $("otp-badge"),
  copyOtp: $("copy-otp"),
  meta: $("meta"),
};

// mode: "auto" (open profile resolved) | "manual" (no open profile)
let state = {
  base: DEFAULT_BASE, link: null, otp: null,
  savedEmail: "", savedPass: "", mode: "manual", autoEmail: "",
  provider: DEFAULT_PROVIDER, amToken: DEFAULT_AM_TOKEN, amSite: DEFAULT_AM_SITE,
  autoProvider: "imap", autoAmId: "", autoSite: "",
};

// ---- storage helpers (fall back to localStorage if chrome.storage missing) ----
function getStored(key, fallback) {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get([key], (res) => resolve(res && res[key] != null ? res[key] : fallback));
    } catch (_e) {
      const v = localStorage.getItem(key);
      resolve(v != null ? v : fallback);
    }
  });
}
function setStored(key, value) {
  try {
    chrome.storage.local.set({ [key]: value });
  } catch (_e) {
    localStorage.setItem(key, value);
  }
}

function normalizeBase(url) {
  let u = (url || "").trim().replace(/\/+$/, "");
  if (!u) u = DEFAULT_BASE;
  if (!/^https?:\/\//i.test(u)) u = "http://" + u;
  return u;
}

function setStatus(msg, kind) {
  els.status.textContent = msg || "";
  els.status.className = "status" + (kind ? " " + kind : "");
}

async function api(path) {
  const res = await fetch(state.base + path, { headers: { Accept: "application/json" } });
  let data = null;
  try { data = await res.json(); } catch (_e) { /* ignore */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data || {};
}

function fmtTime(ts) {
  if (!ts) return "";
  try { return new Date(ts * 1000).toLocaleString(); } catch (_e) { return ""; }
}

// ---- mode switching ----
function showAuto(account, openCount) {
  state.mode = "auto";
  state.autoEmail = account.email;
  state.autoProvider = account.provider || "imap";
  state.autoAmId = account.am_id || "";
  state.autoSite = account.site || "";
  els.autoEmail.textContent = account.email;
  const prov = state.autoProvider === "anymessage" ? "AnyMessage" : "IMAP";
  els.autoNote.textContent = (openCount > 1 ? `1 of ${openCount} open · ` : "open profile · ") + prov;
  els.autoBox.classList.remove("hidden");
  els.manualBox.classList.add("hidden");
  els.contextHint.textContent = openCount > 1
    ? `${openCount} profiles open — using the most recent (${prov}).`
    : `Resolved from the open AdsPower profile (${prov}).`;
  updateReorderVisibility();
}

// Which provider is active for the current lookup (auto uses the open profile's).
function activeProvider() {
  return state.mode === "auto" ? (state.autoProvider || "imap") : (state.provider || "imap");
}

// The Reorder button only makes sense for AnyMessage.
function updateReorderVisibility() {
  if (els.reorderLink) els.reorderLink.classList.toggle("hidden", activeProvider() !== "anymessage");
}

// Show/hide the manual app-password field depending on provider.
function updateManualForProvider() {
  const am = state.provider === "anymessage";
  els.manualPassInput.classList.toggle("hidden", am);
  els.fetchManual.textContent = am ? "Reorder & wait" : "Fetch";
  els.manualEmailInput.placeholder = am ? "email@domain (any)" : "name@gmail.com";
  updateReorderVisibility();
}

function showManual(hint) {
  state.mode = "manual";
  state.autoEmail = "";
  els.autoBox.classList.add("hidden");
  els.manualBox.classList.remove("hidden");
  // seed manual fields from saved defaults if empty
  if (!els.manualEmailInput.value && state.savedEmail) els.manualEmailInput.value = state.savedEmail;
  if (!els.manualPassInput.value && state.savedPass) els.manualPassInput.value = state.savedPass;
  updateManualForProvider();
  const suffix = state.provider === "anymessage" ? "enter an email to reorder." : "enter an email + app password.";
  els.contextHint.textContent = hint || ("No open profile — " + suffix);
}

// ---- load open profile(s) from the companion/backend ----
async function loadContext() {
  stopAmPoll();
  els.contextHint.textContent = "Loading open profile…";
  try {
    const data = await api("/api/ext/context");
    const accounts = (data.accounts || []).filter((a) => a.email);
    if (accounts.length >= 1) {
      showAuto(accounts[0], accounts.length);
      await loadLatest();
    } else {
      showManual("No open profile detected — enter an email + app password.");
      // convenience: if we have saved creds, fetch straight away
      if (state.savedEmail) await loadLatest();
      else setStatus("Manual entry — no profile is open.", "");
    }
  } catch (err) {
    showManual("Backend not reachable — check base URL in ⚙. You can still enter credentials manually.");
    setStatus("Cannot reach backend: " + err.message, "error");
  }
}

function renderLink(link) {
  state.link = link || null;
  if (link) {
    els.linkValue.textContent = link;
    els.linkValue.classList.remove("empty");
    els.openLink.disabled = false;
    els.copyLink.disabled = false;
    els.linkBadge.textContent = "found";
    els.linkBadge.className = "badge fresh";
  } else {
    els.linkValue.textContent = "No link yet";
    els.linkValue.classList.add("empty");
    els.openLink.disabled = true;
    els.copyLink.disabled = true;
    els.linkBadge.textContent = "—";
    els.linkBadge.className = "badge muted";
  }
}

function renderOtp(otp) {
  state.otp = otp || null;
  if (otp) {
    els.otpValue.textContent = otp;
    els.otpValue.classList.remove("empty");
    els.copyOtp.disabled = false;
    els.otpBadge.textContent = "found";
    els.otpBadge.className = "badge fresh";
  } else {
    els.otpValue.textContent = "------";
    els.otpValue.classList.add("empty");
    els.copyOtp.disabled = true;
    els.otpBadge.textContent = "—";
    els.otpBadge.className = "badge muted";
  }
}

// Resolve which account/provider/password to use for a lookup.
function resolveLookup() {
  if (state.mode === "auto" && state.autoEmail) {
    const prov = state.autoProvider || "imap";
    const provName = prov === "anymessage" ? "AnyMessage" : "IMAP";
    return {
      provider: prov,
      account: state.autoEmail,
      password: "", // resolved server-side from the profile remark (IMAP)
      amId: state.autoAmId || "",
      site: state.autoSite || state.amSite || "snapchat.com",
      label: `Auto: ${state.autoEmail} (${provName} open profile)`,
    };
  }
  const prov = state.provider || "imap";
  const provName = prov === "anymessage" ? "AnyMessage" : "IMAP";
  const email = (els.manualEmailInput.value || state.savedEmail || "").trim();
  const password = (els.manualPassInput.value || state.savedPass || "").trim();
  return {
    provider: prov,
    account: email,
    password,
    amId: "",
    site: state.amSite || "snapchat.com",
    label: `Manual (${provName})`,
  };
}

async function loadLatest() {
  stopAmPoll();
  const info = resolveLookup();
  if (info.provider === "anymessage") {
    return readAnyMessage(info); // read-only; use Reorder to fetch a fresh link
  }
  if (!info.account) {
    setStatus("Enter an email to look up.", "error");
    return;
  }
  setStatus(info.label + " · loading…", "loading");
  els.refresh.disabled = true;
  els.fetchManual.disabled = true;
  try {
    let path = "/api/ext/latest?account=" + encodeURIComponent(info.account);
    if (info.password) path += "&password=" + encodeURIComponent(info.password);
    const data = await api(path);
    renderLink(data.link);
    renderOtp(data.otp);
    const bits = [];
    if (data.subject) bits.push(data.subject);
    if (data.from) bits.push("from " + data.from);
    if (data.folder) bits.push(data.folder);
    if (data.ts) bits.push(fmtTime(data.ts));
    els.meta.textContent = bits.join("  ·  ");
    if (!data.link && !data.otp) {
      setStatus(info.label + " · no link/code yet. Try Refresh in a moment.", "");
    } else {
      setStatus(info.label + " · updated " + new Date().toLocaleTimeString(), "");
    }
  } catch (err) {
    setStatus(info.label + " · error: " + err.message, "error");
  } finally {
    els.refresh.disabled = false;
    els.fetchManual.disabled = false;
  }
}

// ---- AnyMessage: reorder the same email, then WAIT for the link ----------- #
// The watch is persisted to chrome.storage.local so it SURVIVES the popup
// closing / losing focus (e.g. you click into the browser to log in). A
// background service worker keeps polling while the popup is shut; on reopen we
// resume from the stored state instead of resetting.
const AM_WAIT_MS = 120000;
let amPollTimer = null;
function stopAmPoll() {
  if (amPollTimer) { clearTimeout(amPollTimer); amPollTimer = null; }
}

function amPath({ email = "", id = "", site = "snapchat.com", reorder = false }) {
  let path = "/api/ext/anymessage?site=" + encodeURIComponent(site || "snapchat.com");
  if (email) path += "&email=" + encodeURIComponent(email);
  if (id) path += "&id=" + encodeURIComponent(id);
  path += "&reorder=" + (reorder ? "1" : "0");
  if (state.amToken) path += "&token=" + encodeURIComponent(state.amToken);
  return path;
}

function haveLabel(w) {
  return (w && w.link && w.otp) ? "link + code" : (w && w.link) ? "link" : (w && w.otp) ? "code" : "";
}
function saveWatch(w) { state.watch = w; setStored("amWatch", JSON.stringify(w)); }
async function loadWatchStored() {
  const raw = await getStored("amWatch", "");
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (_e) { return null; }
}
function clearBadge() { try { chrome.action.setBadgeText({ text: "" }); } catch (_e) { /* ignore */ } }
function notifyBg(type) { try { chrome.runtime.sendMessage({ type }); } catch (_e) { /* ignore */ } }

// Paint the current watch state into the UI.
function applyWatchToUI(w) {
  if (!w) return;
  if (w.link) renderLink(w.link);
  if (w.otp) renderOtp(w.otp);
  els.meta.textContent = [w.email && ("to " + w.email), w.id && ("id " + w.id)].filter(Boolean).join("  ·  ");
  const label = "AnyMessage";
  if (w.status === "reordering") {
    setStatus(label + " · reordering…", "loading");
  } else if (w.status === "waiting") {
    const left = Math.max(0, Math.round(((w.deadline || 0) - Date.now()) / 1000));
    setStatus(label + ` · waiting for ${w.link ? "code" : w.otp ? "link" : "link/code"}… ${left}s` +
      (haveLabel(w) ? ` (have ${haveLabel(w)})` : ""), "loading");
  } else if (w.status === "done") {
    setStatus(label + ` · ${w.link && w.otp ? "link + code ready" : "got " + (haveLabel(w) || "result")}`, "");
  } else if (w.status === "timeout") {
    setStatus(label + ` · timed out${haveLabel(w) ? " — got " + haveLabel(w) : " — nothing arrived"} · press “Reorder & wait”.`, "");
  } else if (w.status === "error") {
    setStatus(label + " · error — press “Reorder & wait”.", "error");
  }
}

// Read-only: show whatever is already in the mailbox for the known activation.
async function readAnyMessage(info) {
  stopAmPoll();
  if (!info.amId) {
    renderLink(null);
    renderOtp(null);
    els.meta.textContent = info.account ? ("email " + info.account) : "";
    setStatus(info.label + " · press “Reorder & wait” to fetch the link.", "");
    return;
  }
  setStatus(info.label + " · reading…", "loading");
  try {
    const d = await api(amPath({ id: info.amId, site: info.site, reorder: false }));
    renderLink(d.link);
    renderOtp(d.otp);
    els.meta.textContent = [d.email && ("to " + d.email), info.amId && ("id " + info.amId)].filter(Boolean).join("  ·  ");
    setStatus(info.label + (d.link ? " · link ready" : " · no link yet — press “Reorder & wait”."), "");
  } catch (err) {
    setStatus(info.label + " · error: " + err.message, "error");
  }
}

// Fast poll loop for while the popup is OPEN. Reads state.watch, updates it in
// storage on every tick so a reopen (or the background worker) stays in sync.
function startPopupPoll() {
  stopAmPoll();
  const tick = async () => {
    let w = state.watch;
    if (!w || !w.active) return;
    if (Date.now() > (w.deadline || 0)) {
      w.active = false;
      w.status = (w.link || w.otp) ? "done" : "timeout";
      w.updatedAt = Date.now();
      saveWatch(w);
      applyWatchToUI(w);
      return;
    }
    applyWatchToUI(w); // live countdown
    try {
      const d = await api(amPath({ id: w.id, site: w.site, reorder: false }));
      if (d.otp && !w.otp) { w.otp = d.otp; renderOtp(d.otp); }
      if (d.link && !w.link) { w.link = d.link; renderLink(d.link); }
      w.status = "waiting";
      if (w.link && w.otp) { w.active = false; w.status = "done"; }
      w.updatedAt = Date.now();
      saveWatch(w);
      applyWatchToUI(w);
      if (!w.active) return;
    } catch (_e) { /* transient — keep polling */ }
    amPollTimer = setTimeout(tick, 5000);
  };
  amPollTimer = setTimeout(tick, 3000);
}

// Reorder the SAME email, persist the watch, then keep waiting (popup + bg).
async function reorderAndWait() {
  stopAmPoll();
  const info = resolveLookup();
  if (info.provider !== "anymessage") return loadLatest();
  if (!info.account && !info.amId) {
    setStatus("Enter an email to reorder.", "error");
    return;
  }
  const busy = (b) => { els.refresh.disabled = b; els.fetchManual.disabled = b; if (els.reorderLink) els.reorderLink.disabled = b; };
  clearBadge();
  let w = {
    active: true, base: state.base, token: state.amToken,
    email: info.account, site: info.site || "snapchat.com", id: info.amId || "",
    deadline: Date.now() + AM_WAIT_MS, link: null, otp: null,
    status: "reordering", updatedAt: Date.now(),
  };
  saveWatch(w);
  applyWatchToUI(w);
  busy(true);
  try {
    // Force a real reorder (re-open the mailbox to catch the NEXT email).
    const data = await api(amPath({ email: info.account, id: info.amId, site: info.site, reorder: true }));
    w.id = data.id || info.amId || "";
    w.email = data.email || w.email;
    state.autoAmId = w.id;
    if (data.otp) { w.otp = data.otp; renderOtp(data.otp); }
    if (data.link) { w.link = data.link; renderLink(data.link); }
    w.status = (w.link && w.otp) ? "done" : "waiting";
    if (w.link && w.otp) w.active = false;
    w.updatedAt = Date.now();
    saveWatch(w);
    applyWatchToUI(w);
  } catch (err) {
    w.active = false; w.status = "error"; w.updatedAt = Date.now();
    saveWatch(w);
    setStatus(info.label + " · reorder error: " + err.message, "error");
    busy(false);
    return;
  }
  busy(false);
  if (!w.id) {
    w.active = false; w.status = "error"; saveWatch(w);
    setStatus(info.label + " · no activation id returned.", "error");
    return;
  }
  if (w.active) {
    notifyBg("amWatchStart"); // keep polling while the popup is closed
    startPopupPoll();         // snappy updates while it's open
  }
}

// On popup open, pick up any in-flight or finished watch instead of resetting.
async function resumeWatch() {
  const w = await loadWatchStored();
  if (!w || !w.id || (!w.active && !w.link && !w.otp)) return false;
  state.watch = w;
  state.autoAmId = w.id || state.autoAmId;
  clearBadge(); // you're looking at it now
  applyWatchToUI(w);
  if (w.active && Date.now() < (w.deadline || 0)) {
    notifyBg("amWatchStart");
    startPopupPoll();
  }
  return true;
}

async function copyText(text, btn) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const old = btn.textContent;
    btn.textContent = "Copied ✓";
    setTimeout(() => (btn.textContent = old), 1200);
  } catch (_e) {
    setStatus("Copy failed — select and copy manually.", "error");
  }
}

function openLink(url) {
  if (!url) return;
  try {
    chrome.tabs.create({ url });
  } catch (_e) {
    window.open(url, "_blank");
  }
}

function renderManualSavedHint() {
  if (state.savedEmail) {
    els.manualSaved.textContent = `Saved: ${state.savedEmail} · password ${state.savedPass ? "••••••••" : "(none)"} — default for manual entry.`;
  } else {
    els.manualSaved.textContent = "Used as the default when no profile is open.";
  }
}

// ---- events ----
els.settingsToggle.addEventListener("click", () => els.settings.classList.toggle("hidden"));

els.saveBase.addEventListener("click", () => {
  state.base = normalizeBase(els.baseUrl.value);
  els.baseUrl.value = state.base;
  setStored("baseUrl", state.base);
  setStatus("Base URL saved.", "");
  els.settings.classList.add("hidden");
  loadContext();
});

els.saveManual.addEventListener("click", () => {
  state.savedEmail = els.manualEmail.value.trim();
  state.savedPass = els.manualPass.value.trim();
  setStored("manualEmail", state.savedEmail);
  setStored("manualPass", state.savedPass);
  renderManualSavedHint();
  // reflect into the manual entry fields
  els.manualEmailInput.value = state.savedEmail;
  els.manualPassInput.value = state.savedPass;
  setStatus(state.savedEmail ? "Manual credentials saved." : "Manual credentials cleared.", "");
  els.settings.classList.add("hidden");
  if (state.mode === "manual") loadLatest();
});

els.clearManual.addEventListener("click", () => {
  state.savedEmail = "";
  state.savedPass = "";
  els.manualEmail.value = "";
  els.manualPass.value = "";
  setStored("manualEmail", "");
  setStored("manualPass", "");
  renderManualSavedHint();
  setStatus("Manual credentials cleared.", "");
});

els.provider.addEventListener("change", () => {
  state.provider = els.provider.value === "anymessage" ? "anymessage" : "imap";
  setStored("provider", state.provider);
  if (state.mode === "manual") updateManualForProvider();
});

els.saveAm.addEventListener("click", () => {
  state.amToken = els.amToken.value.trim();
  state.amSite = (els.amSite.value.trim() || "snapchat.com");
  setStored("amToken", state.amToken);
  setStored("amSite", state.amSite);
  els.amSite.value = state.amSite;
  setStatus(state.amToken ? "AnyMessage settings saved." : "AnyMessage token cleared.", "");
  loadBalance();
});

async function loadBalance() {
  const token = (els.amToken.value.trim() || state.amToken || "").trim();
  if (!token) { els.amBalance.textContent = "no token"; return; }
  els.amBalance.textContent = "checking…";
  try {
    const data = await api("/api/ext/anymessage/balance?token=" + encodeURIComponent(token));
    els.amBalance.textContent = data.ok ? `$${Number(data.balance).toFixed(2)}` : (data.detail || "unavailable");
  } catch (err) {
    els.amBalance.textContent = "error: " + err.message;
  }
}
els.checkBalance.addEventListener("click", loadBalance);

els.refresh.addEventListener("click", () => loadContext());

els.fetchManual.addEventListener("click", () => {
  if (activeProvider() === "anymessage") reorderAndWait();
  else loadLatest();
});
const manualGo = () => { if (activeProvider() === "anymessage") reorderAndWait(); else loadLatest(); };
els.manualEmailInput.addEventListener("keydown", (e) => { if (e.key === "Enter") manualGo(); });
els.manualPassInput.addEventListener("keydown", (e) => { if (e.key === "Enter") manualGo(); });

if (els.reorderLink) els.reorderLink.addEventListener("click", () => reorderAndWait());

// Reflect background-worker updates (link/OTP found while popup was closed) live.
try {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.amWatch && changes.amWatch.newValue) {
      try {
        const w = JSON.parse(changes.amWatch.newValue);
        state.watch = w;
        applyWatchToUI(w);
      } catch (_e) { /* ignore */ }
    }
  });
} catch (_e) { /* chrome.storage unavailable — ignore */ }
els.openLink.addEventListener("click", () => openLink(state.link));
els.copyLink.addEventListener("click", () => copyText(state.link, els.copyLink));
els.copyOtp.addEventListener("click", () => copyText(state.otp, els.copyOtp));

// ---- init ----
(async function init() {
  state.base = normalizeBase(await getStored("baseUrl", DEFAULT_BASE));
  els.baseUrl.value = state.base;
  state.savedEmail = (await getStored("manualEmail", "")) || "";
  state.savedPass = (await getStored("manualPass", "")) || "";
  els.manualEmail.value = state.savedEmail;
  els.manualPass.value = state.savedPass;
  state.provider = (await getStored("provider", DEFAULT_PROVIDER)) || DEFAULT_PROVIDER;
  state.amToken = (await getStored("amToken", DEFAULT_AM_TOKEN)) || DEFAULT_AM_TOKEN;
  state.amSite = (await getStored("amSite", DEFAULT_AM_SITE)) || DEFAULT_AM_SITE;
  els.provider.value = state.provider;
  els.amToken.value = state.amToken;
  els.amSite.value = state.amSite;
  // Persist defaults once so a fresh install doesn't show empty token/provider.
  if (!(await getStored("amToken", ""))) setStored("amToken", state.amToken);
  if (!(await getStored("provider", ""))) setStored("provider", state.provider);
  if (!(await getStored("amSite", ""))) setStored("amSite", state.amSite);
  renderManualSavedHint();
  updateReorderVisibility();
  await loadContext();
  await resumeWatch(); // pick up an in-flight/finished reorder without resetting
})();
