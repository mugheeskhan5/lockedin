"use strict";

const ENDPOINT = "http://127.0.0.1:8000/ingest";
const ALARM = "reels-digest-outbox";
const PREFIX = "pending:";
const BATCH_SIZE = 100;
let writes = Promise.resolve();
let inFlight = null;
let lastAttempt = 0;

function serial(work) {
  const operation = writes.then(work);
  writes = operation.catch(() => {});
  return operation;
}

function validateSender(sender) {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab) {
    throw new Error("Unexpected capture sender");
  }
  const origin = new URL(sender.url || "").origin;
  if (!["https://www.instagram.com", "https://instagram.com"].includes(origin)) {
    throw new Error("Capture sender is not Instagram");
  }
}

function validateEvent(event) {
  if (!event || typeof event.shortcode !== "string" ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(event.shortcode)) throw new Error("Invalid shortcode");
  const permalink = `https://www.instagram.com/reel/${event.shortcode}/`;
  if (event.permalink !== permalink || typeof event.caption !== "string" ||
      typeof event.capturedAt !== "string" || !Number.isFinite(Date.parse(event.capturedAt))) {
    throw new Error("Invalid capture fields");
  }
  const result = {
    shortcode: event.shortcode, permalink,
    caption: event.caption.slice(0, 10000),
    capturedAt: new Date(event.capturedAt).toISOString()
  };
  if (typeof event.thumbnail_url === "string" && event.thumbnail_url.length <= 4096) {
    try {
      const url = new URL(event.thumbnail_url);
      if (url.protocol === "https:" && ["cdninstagram.com", "fbcdn.net", "instagram.com"]
          .some(host => url.hostname === host || url.hostname.endsWith("." + host))) result.thumbnail_url = url.href;
    } catch { /* Optional thumbnail omitted. */ }
  }
  if (typeof event.frame_status === "string") result.frame_status = event.frame_status.slice(0, 32);
  if (typeof event.frame_data_url === "string" && event.frame_data_url.length <= 180000 &&
      event.frame_data_url.startsWith("data:image/jpeg;base64,")) {
    result.frame_data_url = event.frame_data_url;
    result.frame_status = "ok";
  }
  return result;
}

async function enqueue(event) {
  const key = PREFIX + event.shortcode;
  const previous = (await chrome.storage.local.get(key))[key]?.event;
  if (previous) {
    event.capturedAt = previous.capturedAt < event.capturedAt ? previous.capturedAt : event.capturedAt;
    if ((previous.caption || "").length > event.caption.length) event.caption = previous.caption;
    if (!event.thumbnail_url && previous.thumbnail_url) event.thumbnail_url = previous.thumbnail_url;
    if (previous.frame_data_url && !event.frame_data_url) {
      event.frame_data_url = previous.frame_data_url;
      event.frame_status = "ok";
    }
    if (JSON.stringify(previous) === JSON.stringify(event)) return;
  }
  try {
    await chrome.storage.local.set({ [key]: { event, revision: crypto.randomUUID() } });
  } catch (error) {
    if (!event.frame_data_url) throw error;
    console.warn("[Reels Digest] Frame queue write failed; retaining caption:", error.message);
    delete event.frame_data_url;
    event.frame_status = "storage-full";
    await chrome.storage.local.set({ [key]: { event, revision: crypto.randomUUID() } });
  }
}

async function initialize() {
  if (!(await chrome.alarms.get(ALARM))) {
    await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
  }
  // Forward Phase 0 records once, durably queueing before removing old keys.
  const stored = await chrome.storage.local.get(null);
  for (const [key, event] of Object.entries(stored)) {
    if (!key.startsWith("reel:")) continue;
    let valid;
    try { valid = validateEvent(event); }
    catch (error) {
      console.warn("[Reels Digest] Invalid legacy record retained:", key, error.message);
      continue;
    }
    await enqueue(valid);
    await chrome.storage.local.remove(key);
  }
}

const ready = initialize();
ready.catch(error => console.warn("[Reels Digest] Queue initialization failed:", error.message));

async function deliver() {
  await ready;
  const batch = await serial(async () => {
    const selected = [];
    let bytes = 0;
    for (const item of Object.entries(await chrome.storage.local.get(null))) {
      if (!item[0].startsWith(PREFIX)) continue;
      const size = new TextEncoder().encode(JSON.stringify(item[1].event)).length;
      if (selected.length && (bytes + size > 1500000 || selected.length >= BATCH_SIZE)) break;
      selected.push(item); bytes += size;
    }
    return selected;
  });
  if (!batch.length) return;
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    redirect: "error",
    signal: AbortSignal.timeout(8000),
    body: JSON.stringify({ events: batch.map(([, value]) => value.event) })
  });
  if (!response.ok) throw new Error(`Ingest HTTP ${response.status}; batch retained`);
  const receipt = await response.json();
  if (!Array.isArray(receipt.accepted) || !Array.isArray(receipt.rejected)) {
    throw new Error("Invalid ingest receipt; batch retained");
  }
  const accepted = new Set(receipt.accepted);
  const rejected = new Map(receipt.rejected.map(item => [item.index, item]));
  // Never erase a newer caption revision that arrived during the POST.
  await serial(async () => {
    const current = await chrome.storage.local.get(batch.map(([key]) => key));
    const remove = [];
    for (const [index, [key, value]] of batch.entries()) {
      if (current[key]?.revision !== value.revision) continue;
      if (accepted.has(value.event.shortcode)) remove.push(key);
      else if (rejected.has(index)) {
        const reason = rejected.get(index).error;
        await chrome.storage.local.set({
          [`rejected:${value.event.shortcode}`]: { event: value.event, reason }
        });
        console.warn("[Reels Digest] Backend rejected row:", value.event.shortcode, reason);
        remove.push(key);
      }
    }
    if (remove.length) await chrome.storage.local.remove(remove);
  });
  console.info(`[Reels Digest] Ingest: ${receipt.inserted} new, ${receipt.deduped} existing, ${receipt.rejected.length} rejected.`);
}

function flush() {
  if (inFlight) return inFlight;
  if (Date.now() - lastAttempt < 10000) return Promise.resolve();
  lastAttempt = Date.now();
  inFlight = deliver().catch(error => {
    console.warn("[Reels Digest] Delivery deferred; unsent records retained:", error.message);
  }).finally(() => { inFlight = null; });
  return inFlight;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["REEL_CAPTURED", "FLUSH_QUEUE"].includes(message?.type)) return false;
  let event;
  try {
    validateSender(sender);
    if (message.type === "REEL_CAPTURED") event = validateEvent(message.event);
  } catch (error) {
    sendResponse({ ok: false, error: error.message });
    return false;
  }
  const operation = message.type === "FLUSH_QUEUE" ? flush() :
    ready.then(() => serial(() => enqueue(event)));
  operation.then(
    () => sendResponse({ ok: true }),
    error => {
      console.warn("[Reels Digest] Queue write failed:", error.message);
      sendResponse({ ok: false, error: error.message });
    }
  );
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === ALARM) void flush();
});
chrome.runtime.onStartup.addListener(() => { void flush(); });
chrome.runtime.onInstalled.addListener(() => { void flush(); });
void ready.then(() => flush()).catch(() => {});

// Auto-scroll control support. The capture queue and ingest protocol are unchanged.
let healthCache = null;
let healthCheckedAt = 0;
async function backendHealth() {
  if (healthCache && Date.now() - healthCheckedAt < 3000) return healthCache;
  try {
    const response = await fetch("http://127.0.0.1:8000/health", {
      credentials: "omit", redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(3000)
    });
    const body = response.ok ? await response.json() : null;
    healthCache = body?.status === "ok" ? { ok: true, reels: body.reels } : { ok: false };
  } catch { healthCache = { ok: false }; }
  healthCheckedAt = Date.now();
  return healthCache;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["AUTO_REPORT", "AUTO_BACKEND_CHECK", "POPUP_BACKEND_CHECK"].includes(message?.type)) return false;
  try {
    if (message.type === "POPUP_BACKEND_CHECK") {
      if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) {
        throw new Error("Unexpected popup sender");
      }
    } else validateSender(sender);
  } catch (error) {
    sendResponse({ ok: false, error: error.message });
    return false;
  }
  const work = message.type === "AUTO_REPORT" ? Promise.all([
    chrome.action.setBadgeText({ tabId: sender.tab.id, text: message.running === true ? "AUTO" : "" }),
    chrome.action.setBadgeBackgroundColor({ tabId: sender.tab.id, color: "#0f766e" })
  ]).then(() => ({ ok: true })) : backendHealth();
  work.then(sendResponse, () => sendResponse({ ok: false }));
  return true;
});
