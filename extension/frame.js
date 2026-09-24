/* Read one already displayed video frame. Never reload, seek, play, or change
 * crossorigin attributes to obtain it. No DOM selectors belong in this file. */
(() => {
  "use strict";
  function capture(video) {
    if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      return { frame_status: "not-ready" };
    }
    try {
      const canvas = document.createElement("canvas");
      const scale = Math.min(1, 720 / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return { frame_status: "unavailable" };
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      // Reading is allowed to fail with SecurityError on a tainted canvas.
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let sum = 0, squares = 0, count = 0;
      for (let i = 0; i < pixels.length; i += 64) {
        const value = (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3;
        sum += value; squares += value * value; count += 1;
      }
      if (!count || squares / count - (sum / count) ** 2 < 4) return { frame_status: "empty" };
      const data = canvas.toDataURL("image/jpeg", 0.65);
      if (!data.startsWith("data:image/jpeg;base64,") || data.length > 180000) {
        return { frame_status: "too-large" };
      }
      return { frame_status: "ok", frame_data_url: data };
    } catch (error) {
      return { frame_status: error.name === "SecurityError" ? "tainted" : "error" };
    }
  }
  globalThis.ReelsDigestFrame = Object.freeze({ capture });
})();
