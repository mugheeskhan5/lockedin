/* Instagram DOM adapter. Keep ALL markup selectors and extraction here.
 * No private API, page scripts or network interception. Scroll geometry also lives here.
 * Instagram changes markup: adapt this file after a manual capture failure.
 */
(() => {
  "use strict";
  const SELECTORS = Object.freeze({
    video: "video",
    editable: 'input, textarea, select, [contenteditable="true"], [role="textbox"]',
    overlay: '[role="dialog"], [role="menu"], [role="listbox"]',
    images: "img[src]",
    reelLinks: 'a[href*="/reel/"], a[href*="/reels/"]',
    caption: '[data-testid="post-caption"], h1, h2',
    captionFallback: 'span[dir="auto"], div[dir="auto"]',
    controls: 'button, [role="button"], nav, [role="navigation"]',
    link: "a",
    excluded: '[role="dialog"][aria-label*="comment" i]'
  });

  function parseReel(value) {
    try {
      const url = new URL(value, location.origin);
      if (!["www.instagram.com", "instagram.com"].includes(url.hostname)) return null;
      const match = url.pathname.match(/^\/reels?\/([A-Za-z0-9_-]+)\/?$/);
      if (!match) return null;
      return { shortcode: match[1], permalink: `https://www.instagram.com/reel/${match[1]}/` };
    } catch { return null; }
  }

  function visibleArea(element) {
    const style = getComputedStyle(element);
    if (style.visibility !== "visible" || Number(style.opacity) === 0) return 0;
    const r = element.getBoundingClientRect();
    if (r.width < 100 || r.height < 100) return 0;
    const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
    const top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
    const area = Math.max(0, right - left) * Math.max(0, bottom - top);
    const possible = Math.min(r.width, innerWidth) * Math.min(r.height, innerHeight);
    if (area < possible * 0.55) return 0;
    // Hit-testing also excludes offscreen preload videos and covered media.
    const front = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
    if (!front) return 0;
    if (front === element || element.contains(front)) return area;
    // Controls may be nested several levels beside the video. The previous
    // immediate-parent check wrongly treated these local overlays as blockers.
    // Accept a shared single-video container, stopping before the reel feed
    // or document root so unrelated dialogs cannot expose the video behind them.
    for (let scope = element.parentElement;
         scope && scope !== document.body && scope !== document.documentElement;
         scope = scope.parentElement) {
      if (scope.querySelectorAll(SELECTORS.video).length !== 1) break;
      if (scope.contains(front)) return area;
    }
    return 0;
  }

  function captionFrom(scope) {
    const clean = node => (node.innerText || "").replace(/\s+/g, " ").trim();
    const useful = node => {
      if (node.closest(SELECTORS.controls) || node.closest(SELECTORS.link)) return false;
      const text = clean(node);
      return /\p{L}/u.test(text) && !/^(follow|following|more|less|see more|original audio|liked by.*|[\d.,]+[KMB]?)$/i.test(text);
    };
    const preferred = [...scope.querySelectorAll(SELECTORS.caption)].filter(useful);
    const candidates = preferred.length ? preferred :
      [...scope.querySelectorAll(SELECTORS.captionFallback)]
        .filter(node => !node.querySelector(SELECTORS.captionFallback) && useful(node));
    return candidates.map(clean).sort((a, b) => b.length - a.length)[0]?.slice(0, 10000) || "";
  }

  function extract(video) {
    let scope = video.parentElement;
    let linked = null;
    // Stop before crossing into a sibling reel. This bounds caption extraction
    // and prevents attributing another reel's link to the visible video.
    for (let parent = scope; parent && parent !== document.body; parent = parent.parentElement) {
      if (parent.querySelectorAll(SELECTORS.video).length !== 1) break;
      const links = [...parent.querySelectorAll(SELECTORS.reelLinks)]
        .map(a => parseReel(a.getAttribute("href"))).filter(Boolean);
      const codes = new Set(links.map(link => link.shortcode));
      if (codes.size > 1) break;
      scope = parent;
      if (codes.size === 1) linked = links[0];
    }
    // Fullscreen reel viewers sometimes expose identity only in the SPA URL.
    // Used only for the foremost visible video, never for preload candidates.
    const identity = linked || parseReel(location.href);
    if (!identity || !scope) return null;
    let thumbnail = video.poster;
    if (!thumbnail) {
      const cover = [...scope.querySelectorAll(SELECTORS.images)].find(img => {
        const rect = img.getBoundingClientRect();
        return rect.width >= 120 && rect.height >= 120 &&
          Math.abs(rect.width / rect.height - video.getBoundingClientRect().width /
            Math.max(1, video.getBoundingClientRect().height)) < 0.3;
      });
      thumbnail = cover?.currentSrc || cover?.src;
    }
    let thumbnail_url = null;
    try {
      const url = new URL(thumbnail);
      if (url.protocol === "https:" && ["cdninstagram.com", "fbcdn.net", "instagram.com"]
          .some(host => url.hostname === host || url.hostname.endsWith("." + host))) {
        thumbnail_url = url.href;
      }
    } catch { /* No usable public poster; frame or text-only fallback later. */ }
    return { ...identity, caption: captionFrom(scope), thumbnail_url };
  }

  function currentReel() {
    if (document.visibilityState !== "visible") return null;
    const visible = [...document.querySelectorAll(SELECTORS.video)]
      .filter(video => !video.closest(SELECTORS.excluded))
      .map(video => ({ video, area: visibleArea(video) }))
      .filter(item => item.area > 0).sort((a, b) => b.area - a.area);
    if (!visible.length) return null;
    const video = visible[0].video;
    const reel = extract(video);
    return reel ? { ...reel, video } : null;
  }

  function isReelsPage() {
    return /^\/reels?(?:\/[A-Za-z0-9_-]+)?\/?$/.test(location.pathname);
  }

  function scrollContainer(video) {
    for (let node = video.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (/(auto|scroll)/.test(style.overflowY) &&
          node.clientHeight >= 100 && node.scrollHeight > node.clientHeight + 4) return node;
    }
    const root = document.scrollingElement;
    return root && root.scrollHeight > root.clientHeight + 4 &&
      !/(hidden|clip)/.test(getComputedStyle(root).overflowY) ? root : null;
  }

  function scrollPlan() {
    if (!isReelsPage()) return { error: "Open Instagram's Reels viewer first." };
    const reel = currentReel();
    if (!reel) return { error: "No visible reel found. Close overlays and open a reel." };
    if (document.activeElement?.matches(SELECTORS.editable)) {
      return { error: "Finish typing before starting auto-scroll." };
    }
    const blocked = [...document.querySelectorAll(SELECTORS.overlay)].some(node => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return !node.contains(reel.video) && style.visibility === "visible" &&
        style.display !== "none" && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0 &&
        rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    });
    if (blocked) return { error: "Close the open dialog or menu before auto-scrolling." };
    const target = scrollContainer(reel.video);
    if (!target) return { error: "No scrollable reel feed found. Open the full Reels feed." };
    const rect = reel.video.getBoundingClientRect();
    // Use the next mounted reel's spacing when available. All Instagram
    // selectors and layout assumptions remain confined to this adapter.
    const next = [...target.querySelectorAll(SELECTORS.video)]
      .filter(video => video !== reel.video && scrollContainer(video) === target)
      .map(video => video.getBoundingClientRect())
      .filter(other => other.width >= 100 && other.height >= 100 &&
        other.top - rect.top > rect.height * 0.6 &&
        Math.abs((other.left + other.width / 2) - (rect.left + rect.width / 2)) < rect.width / 2)
      .sort((a, b) => a.top - b.top)[0];
    const delta = next ? next.top - rect.top : Math.min(target.clientHeight, innerHeight);
    if (delta <= 0 || delta > 2 * innerHeight) return { error: "Reel layout is not supported." };
    return { shortcode: reel.shortcode, target, delta };
  }

  globalThis.ReelsDigestDOM = Object.freeze({ currentReel, isReelsPage, scrollPlan });
})();
