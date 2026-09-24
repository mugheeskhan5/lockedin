/* Explicitly enabled, fixed-interval scrolling. No simulated input/timing,
 * playback control, login automation, private APIs or detection workarounds. */
(() => {
  "use strict";
  if (globalThis.__reelsDigestAutoScroll) return;
  globalThis.__reelsDigestAutoScroll = true;
  let timer = null;
  let generation = 0;
  let working = false;
  let awaitingCode = null;
  let stalled = 0;
  let captureWaits = 0;
  let lastHealth = 0;
  const seen = new Set();
  const state = {
    running: false, reason: "Ready. Auto-scroll starts only when you press Start.",
    intervalSeconds: 10, durationMinutes: 10, endsAt: 0, nextAt: 0,
    scrolls: 0, observedReels: 0
  };

  function snapshot() { return { ...state }; }

  function report() {
    try {
      chrome.runtime.sendMessage({ type: "AUTO_REPORT", running: state.running }).catch(() => {});
    } catch { /* Extension invalidated: the next tick stops this session. */ }
  }

  function stop(reason = "Stopped by you.") {
    generation += 1;
    if (timer) clearInterval(timer);
    timer = null;
    state.running = false;
    state.nextAt = 0;
    state.reason = reason;
    report();
    console.info("[Reels Digest] Auto-scroll stopped:", reason);
    return snapshot();
  }

  async function backendReady() {
    const reply = await chrome.runtime.sendMessage({ type: "AUTO_BACKEND_CHECK" });
    return reply?.ok === true;
  }

  async function start(options) {
    if (state.running) return snapshot();
    const interval = Number(options?.intervalSeconds);
    const duration = Number(options?.durationMinutes);
    if (!Number.isInteger(interval) || interval < 5 || interval > 120 ||
        !Number.isInteger(duration) || duration < 1 || duration > 60) {
      throw new Error("Choose 5–120 seconds and 1–60 minutes.");
    }
    if (document.visibilityState !== "visible") throw new Error("Keep the Instagram tab visible.");
    const plan = globalThis.ReelsDigestDOM.scrollPlan();
    if (plan.error) throw new Error(plan.error);
    const token = ++generation;
    if (!(await backendReady())) throw new Error("Start the local backend on port 8000 first.");
    if (token !== generation || document.visibilityState !== "visible") return snapshot();
    const now = Date.now();
    seen.clear();
    seen.add(plan.shortcode);
    awaitingCode = null;
    stalled = 0;
    captureWaits = 0;
    lastHealth = now;
    Object.assign(state, {
      running: true, reason: "Running at a fixed interval.", intervalSeconds: interval,
      durationMinutes: duration, endsAt: now + duration * 60000,
      nextAt: now + interval * 1000, scrolls: 0, observedReels: 1
    });
    timer = setInterval(() => { void tick(); }, 1000);
    report();
    console.info(`[Reels Digest] Auto-scroll started: every ${interval}s for ${duration} minute(s).`);
    return snapshot();
  }

  async function tick() {
    if (!state.running || working) return;
    const token = generation;
    working = true;
    try {
      if (!chrome.runtime?.id) return stop("Extension reloaded. Refresh this tab.");
      if (document.visibilityState !== "visible") return stop("Tab hidden. Return here and press Start to resume.");
      if (!globalThis.ReelsDigestDOM.isReelsPage()) return stop("Left the Reels viewer.");
      const now = Date.now();
      if (now >= state.endsAt) return stop("Session finished.");
      if (now < state.nextAt) return;
      // No catch-up bursts after browser throttling or sleep.
      state.nextAt = now + state.intervalSeconds * 1000;
      if (now - lastHealth >= 30000) {
        const online = await backendReady();
        if (!state.running || token !== generation) return;
        if (!online) return stop("Backend is offline. Captures remain queued; restart it before resuming.");
        lastHealth = Date.now();
      }
      const plan = globalThis.ReelsDigestDOM.scrollPlan();
      if (plan.error) return stop(plan.error);
      if (awaitingCode === plan.shortcode) {
        stalled += 1;
        if (stalled >= 3) return stop("No new reel after three attempts. The feed may have ended or its layout changed.");
      } else {
        stalled = 0;
      }
      seen.add(plan.shortcode);
      state.observedReels = seen.size;
      const captured = await globalThis.ReelsDigestCapture.checkpoint(plan.shortcode);
      if (!state.running || token !== generation) return;
      if (!captured) {
        captureWaits += 1;
        state.reason = "Waiting for the current reel to enter the capture queue.";
        if (captureWaits >= 3) stop("Capture could not be saved. Check the extension's errors before resuming.");
        return;
      }
      const fresh = globalThis.ReelsDigestDOM.scrollPlan();
      if (fresh.error) return stop(fresh.error);
      if (fresh.shortcode !== plan.shortcode) return;
      if (Date.now() >= state.endsAt) return stop("Session finished.");
      captureWaits = 0;
      awaitingCode = plan.shortcode;
      fresh.target.scrollBy({ top: fresh.delta, left: 0, behavior: "instant" });
      state.scrolls += 1;
      state.reason = "Running at a fixed interval.";
    } catch (error) {
      if (token === generation && state.running) {
        stop("Auto-scroll stopped: " + (error.message || "unexpected error"));
      }
    } finally {
      working = false;
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!["AUTO_STATUS", "AUTO_START", "AUTO_STOP"].includes(message?.type)) return false;
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) return false;
    const operation = message.type === "AUTO_START" ? start(message.options) :
      Promise.resolve(message.type === "AUTO_STOP" ? stop() : snapshot());
    operation.then(value => sendResponse({ ok: true, state: value }),
      error => sendResponse({ ok: false, error: error.message }));
    return true;
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") stop("Tab hidden. Return here and press Start to resume.");
  });
  for (const type of ["wheel", "touchstart", "pointerdown"]) {
    document.addEventListener(type, event => {
      if (event.isTrusted && state.running) stop("Manual interaction detected.");
    }, { passive: true, capture: true });
  }
  document.addEventListener("keydown", event => {
    if (event.isTrusted && state.running &&
        ["Escape", "ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "].includes(event.key)) {
      stop(event.key === "Escape" ? "Stopped with Escape." : "Manual interaction detected.");
    }
  }, true);
  window.addEventListener("pagehide", () => stop("Page closed or navigated away."));
  report();
})();
