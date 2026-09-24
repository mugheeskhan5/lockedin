/* Capture observer. Optional scrolling is controlled separately in autoscroll.js. */
(() => {
  "use strict";
  if (globalThis.__reelsDigestCapture) return;
  globalThis.__reelsDigestCapture = true;
  const saved = new Map();
  const pending = new Set();
  let scheduled = false;
  let stopped = false;
  let retryAfter = 0;
  let lastWarning = 0;

  function warn(error) {
    if (Date.now() - lastWarning > 5000) {
      console.warn("[Reels Digest] Capture skipped:", error.message || error);
      lastWarning = Date.now();
    }
  }

  async function capture() {
    if (stopped || document.visibilityState !== "visible" || Date.now() < retryAfter) return;
    let reel;
    try {
      const found = globalThis.ReelsDigestDOM.currentReel();
      if (!found) return;
      const { video, ...metadata } = found;
      reel = metadata;
      if (!reel || pending.has(reel.shortcode)) return;
      const previous = saved.get(reel.shortcode);
      // Re-read late captions and expanded text, keeping the first capture time.
      const retryFrame = previous?.frameStatus === "not-ready" &&
        previous.frameAttempts < 3 && Date.now() - previous.frameAttemptAt >= 1000;
      const thumbnailChanged = reel.thumbnail_url && reel.thumbnail_url !== previous?.thumbnail_url;
      if (previous && previous.caption.length >= reel.caption.length && !retryFrame && !thumbnailChanged) return;
      pending.add(reel.shortcode);
      const frame = !previous || retryFrame ? globalThis.ReelsDigestFrame.capture(video) : {};
      if (frame.frame_status && frame.frame_status !== "ok") {
        console.info(`[Reels Digest] Frame skipped (${frame.frame_status}); caption fallback for ${reel.shortcode}.`);
      }
      const event = { ...reel, ...frame, capturedAt: previous?.capturedAt || new Date().toISOString() };
      const reply = await chrome.runtime.sendMessage({ type: "REEL_CAPTURED", event });
      if (!reply?.ok) throw new Error(reply?.error || "Storage did not acknowledge capture");
      // Keep frame bytes only in the durable delivery queue, never this cache.
      saved.set(reel.shortcode, {
        ...reel, capturedAt: event.capturedAt,
        frameStatus: frame.frame_status || previous?.frameStatus,
        frameAttempts: (previous?.frameAttempts || 0) + (frame.frame_status ? 1 : 0),
        frameAttemptAt: frame.frame_status ? Date.now() : previous?.frameAttemptAt
      });
      // The backend is authoritative; bound only the in-page optimization cache.
      if (saved.size > 2000) saved.delete(saved.keys().next().value);
    } catch (error) {
      warn(error);
      retryAfter = Date.now() + 5000;
      if (!chrome.runtime?.id || /context invalidated/i.test(error.message || "")) {
        stopped = true;
        observer.disconnect();
        clearInterval(poll);
        clearInterval(delivery);
        console.warn("[Reels Digest] Extension reloaded; refresh this Instagram tab.");
      }
    } finally {
      if (reel) pending.delete(reel.shortcode);
    }
  }

  function schedule() {
    if (scheduled || stopped || document.visibilityState !== "visible") return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; void capture(); });
  }

  // Observe manual or opted-in scrolling, recycled nodes, and lazy-loaded captions.
  // Fixed polling only reads DOM/URL changes made by Instagram's SPA router.
  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, {
    childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ["href", "src"]
  });
  document.addEventListener("scroll", schedule, { passive: true, capture: true });
  document.addEventListener("visibilitychange", schedule);
  window.addEventListener("resize", schedule, { passive: true });
  window.addEventListener("popstate", schedule);
  const poll = setInterval(schedule, 500);
  // Local delivery only: never interacts with Instagram. A worker alarm also
  // retries queued records when the tab is closed.
  const delivery = setInterval(() => {
    if (stopped || document.visibilityState !== "visible") return;
    chrome.runtime.sendMessage({ type: "FLUSH_QUEUE" }).catch(warn);
  }, 10000);
  console.info(`[Reels Digest] Capture active (v${chrome.runtime.getManifest().version}).`);
  // Auto-scroll waits until this reel has a durable queue acknowledgement.
  // The worker/backend remain responsible for delivery, retry and deduplication.
  globalThis.ReelsDigestCapture = Object.freeze({
    checkpoint: async shortcode => {
      await capture();
      return !stopped && saved.has(shortcode);
    }
  });
  schedule();
})();
