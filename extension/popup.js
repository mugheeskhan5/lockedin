"use strict";
const ui = Object.fromEntries(["controls", "interval", "duration", "start", "stop", "mode", "reason", "counts", "countdown", "backend", "error"]
  .map(id => [id, document.getElementById(id)]));
let tabId = null;
let current = null;
let sending = false;
let polling = false;
let backendOnline = false;
let healthAt = 0;

function errorText(text) {
  ui.error.textContent = text || "";
  ui.error.hidden = !text;
}

function render() {
  const running = current?.running === true;
  ui.mode.textContent = running ? "Running" : current ? "Stopped" : "Open Instagram Reels";
  ui.reason.textContent = current?.reason || "Open a reel, then refresh Instagram after updating this extension.";
  ui.start.disabled = sending || !current || running || !backendOnline;
  ui.stop.disabled = sending || !running;
  ui.interval.disabled = running || sending;
  ui.duration.disabled = running || sending;
  if (running) {
    const remaining = Math.max(0, Math.ceil((current.endsAt - Date.now()) / 1000));
    ui.countdown.textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")} left`;
  } else ui.countdown.textContent = "";
  ui.counts.textContent = current?.observedReels ? `${current.observedReels} distinct reels observed · ${current.scrolls} scroll requests` : "No session started.";
}

async function tabMessage(type, options) {
  if (!Number.isInteger(tabId)) throw new Error("Open an Instagram Reels tab first.");
  const response = await chrome.tabs.sendMessage(tabId, { type, options }, { frameId: 0 });
  if (!response?.ok) throw new Error(response?.error || "This tab did not respond. Refresh Instagram.");
  current = response.state;
  if (current.running) {
    ui.interval.value = current.intervalSeconds;
    ui.duration.value = current.durationMinutes;
  }
  return current;
}

async function refresh() {
  if (polling || sending) return;
  polling = true;
  try {
    try { await tabMessage("AUTO_STATUS"); }
    catch { current = null; }
    if (Date.now() - healthAt > 5000) {
      const health = await chrome.runtime.sendMessage({ type: "POPUP_BACKEND_CHECK" });
      backendOnline = health?.ok === true;
      ui.backend.textContent = backendOnline ? `Backend online · ${health.reels} reels saved in database` : "Backend offline · start it on port 8000.";
      healthAt = Date.now();
    }
  } catch {
    backendOnline = false;
    ui.backend.textContent = "Connection unavailable. Reload the extension and refresh Instagram.";
  } finally {
    polling = false;
    render();
  }
}

ui.controls.addEventListener("submit", async event => {
  event.preventDefault();
  if (sending) return;
  sending = true;
  errorText("");
  render();
  try {
    const options = { intervalSeconds: Number(ui.interval.value), durationMinutes: Number(ui.duration.value) };
    await tabMessage("AUTO_START", options);
    // Persist preferences only. A running session is never restored or auto-started.
    await chrome.storage.local.set({ autoScrollPreferences: options });
  } catch (error) { errorText(error.message); }
  finally { sending = false; render(); }
});
ui.stop.addEventListener("click", async () => {
  sending = true;
  render();
  try { await tabMessage("AUTO_STOP"); errorText(""); }
  catch (error) { errorText(error.message); }
  finally { sending = false; render(); }
});

(async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    tabId = tab?.id;
    const preferences = (await chrome.storage.local.get("autoScrollPreferences")).autoScrollPreferences;
    if (preferences && Number.isInteger(preferences.intervalSeconds) && preferences.intervalSeconds >= 5 && preferences.intervalSeconds <= 120 &&
        Number.isInteger(preferences.durationMinutes) && preferences.durationMinutes >= 1 && preferences.durationMinutes <= 60) {
      ui.interval.value = preferences.intervalSeconds;
      ui.duration.value = preferences.durationMinutes;
    }
    await refresh();
    setInterval(() => { void refresh(); }, 1000);
  } catch (error) { errorText(error.message); render(); }
})();
