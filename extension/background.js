"use strict";

// Keeps an AnyMessage "reorder & wait" alive even when the popup is closed or
// loses focus (e.g. you clicked into the browser to log in). The popup writes a
// `amWatch` object to chrome.storage.local; this worker polls the companion on
// an alarm, records the link/OTP as they arrive, updates the toolbar badge and
// fires a notification — so nothing resets while you're busy elsewhere.

const POLL_ALARM = "amPoll";

function getWatch() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["amWatch"], (x) => {
      try { resolve(x && x.amWatch ? JSON.parse(x.amWatch) : null); }
      catch (_e) { resolve(null); }
    });
  });
}

function setWatch(w) {
  return new Promise((resolve) => chrome.storage.local.set({ amWatch: JSON.stringify(w) }, () => resolve()));
}

function amUrl(w) {
  const base = (w.base || "http://127.0.0.1:8799").replace(/\/+$/, "");
  let p = base + "/api/ext/anymessage?site=" + encodeURIComponent(w.site || "snapchat.com") +
          "&id=" + encodeURIComponent(w.id) + "&reorder=0";
  if (w.token) p += "&token=" + encodeURIComponent(w.token);
  return p;
}

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeText({ text: text || "" });
    if (color) await chrome.action.setBadgeBackgroundColor({ color });
  } catch (_e) { /* ignore */ }
}

function notify(title, message) {
  try {
    chrome.notifications.create("am_" + Date.now(), {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title,
      message: message || "",
      priority: 2,
    });
  } catch (_e) { /* ignore */ }
}

async function pollOnce() {
  const w = await getWatch();
  if (!w || !w.active || !w.id) { await chrome.alarms.clear(POLL_ALARM); return; }
  if (Date.now() > (w.deadline || 0)) {
    w.active = false;
    w.status = (w.link || w.otp) ? "done" : "timeout";
    w.updatedAt = Date.now();
    await setWatch(w);
    await chrome.alarms.clear(POLL_ALARM);
    await setBadge(w.link ? "✓" : (w.otp ? "#" : ""), "#22c55e");
    return;
  }
  try {
    const res = await fetch(amUrl(w), { headers: { Accept: "application/json" } });
    const d = await res.json();
    let got = false;
    if (d && d.ok) {
      if (d.otp && !w.otp) { w.otp = d.otp; got = true; }
      if (d.link && !w.link) { w.link = d.link; got = true; }
    }
    w.status = "waiting";
    w.updatedAt = Date.now();
    if (w.link && w.otp) { w.active = false; w.status = "done"; }
    await setWatch(w);
    if (w.link) await setBadge("✓", "#22c55e");
    else if (w.otp) await setBadge("#", "#f59e0b");
    if (got) {
      if (w.link && w.otp) notify("Snapchat: link + code ready", w.email || "");
      else if (w.link) notify("Snapchat verification link ready", w.email || "");
      else if (w.otp) notify("Snapchat code ready: " + w.otp, w.email || "");
    }
    if (!w.active) await chrome.alarms.clear(POLL_ALARM);
  } catch (_e) { /* transient — keep the alarm going */ }
}

chrome.alarms.onAlarm.addListener((a) => { if (a.name === POLL_ALARM) pollOnce(); });

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "amWatchStart") {
    chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 }); // 30s safety net
    pollOnce();
    sendResponse && sendResponse({ ok: true });
  } else if (msg && msg.type === "amWatchStop") {
    chrome.alarms.clear(POLL_ALARM);
    setBadge("", "");
    sendResponse && sendResponse({ ok: true });
  }
  return true;
});

async function reviveIfActive() {
  const w = await getWatch();
  if (w && w.active) chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
}
chrome.runtime.onStartup.addListener(reviveIfActive);
chrome.runtime.onInstalled.addListener(reviveIfActive);
