(() => {
  "use strict";

  const API = {
    info: "/api/info",
    process: "/api/process",
    status: (id) => `/api/status/${id}`,
    download: (id) => `/api/download/${id}`,
  };

  // ---- Element refs -------------------------------------------------------
  const els = {
    urlInput: document.getElementById("url-input"),
    loadBtn: document.getElementById("load-btn"),
    urlError: document.getElementById("url-error"),

    cardPreview: document.getElementById("card-preview"),
    thumbWrap: document.getElementById("thumb-wrap"),
    thumbImg: document.getElementById("thumb-img"),
    cropBox: document.getElementById("crop-box"),
    cropToggle: document.getElementById("crop-toggle"),
    cropReadout: document.getElementById("crop-readout"),
    videoTitle: document.getElementById("video-title"),
    videoSource: document.getElementById("video-source"),
    videoDuration: document.getElementById("video-duration"),
    videoDims: document.getElementById("video-dims"),

    cardTimeline: document.getElementById("card-timeline"),
    filmstrip: document.getElementById("filmstrip"),
    track: document.querySelector(".track"),
    rangeFill: document.getElementById("range-fill"),
    handleIn: document.getElementById("handle-in"),
    handleOut: document.getElementById("handle-out"),
    tcIn: document.getElementById("tc-in"),
    tcOut: document.getElementById("tc-out"),
    tcClipDuration: document.getElementById("tc-clip-duration"),

    cardOutput: document.getElementById("card-output"),
    containerChips: document.getElementById("container-chips"),
    qualityChips: document.getElementById("quality-chips"),
    confirmPermission: document.getElementById("confirm-permission"),
    renderBtn: document.getElementById("render-btn"),
    processError: document.getElementById("process-error"),

    cardProgress: document.getElementById("card-progress"),
    progressHeading: document.getElementById("progress-heading"),
    tapeFill: document.getElementById("tape-fill"),
    progressMessage: document.getElementById("progress-message"),
    resultRow: document.getElementById("result-row"),
    resultName: document.getElementById("result-name"),
    resultSize: document.getElementById("result-size"),
    downloadLink: document.getElementById("download-link"),
  };

  // ---- State ----------------------------------------------------------
  const state = {
    url: "",
    duration: 0,
    nativeWidth: null,
    nativeHeight: null,
    clipStart: 0,
    clipEnd: 0,
    cropEnabled: false,
    crop: null, // { x, y, width, height } in native video pixels
    container: "mp4",
    quality: "best",
    pollTimer: null,
  };

  // ---- Helpers ----------------------------------------------------------
  function fmtTime(totalSeconds) {
    const s = Math.max(0, totalSeconds);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const secStr = sec.toFixed(1).padStart(4, "0");
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${secStr}`;
  }

  function fmtBytes(bytes) {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let n = bytes;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${n.toFixed(1)} ${units[i]}`;
  }

  function showError(el, message) {
    el.textContent = message;
    el.hidden = false;
  }

  function clearError(el) {
    el.hidden = true;
    el.textContent = "";
  }

  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Request failed (${res.status})`);
    }
    return data;
  }

  // ---- Step 1: load video info -------------------------------------------
  async function loadVideo() {
    const url = els.urlInput.value.trim();
    clearError(els.urlError);
    if (!url) {
      showError(els.urlError, "Paste a video URL first.");
      return;
    }

    els.loadBtn.disabled = true;
    els.loadBtn.querySelector(".btn-label").textContent = "Loading…";
    els.loadBtn.querySelector(".btn-spinner").hidden = false;

    try {
      const info = await apiPost(API.info, { url });
      state.url = url;
      state.duration = info.duration || 0;
      state.nativeWidth = info.width;
      state.nativeHeight = info.height;
      state.clipStart = 0;
      state.clipEnd = state.duration || 10;

      els.videoTitle.textContent = info.title;
      els.videoSource.textContent = info.extractor || "—";
      els.videoDuration.textContent = fmtTime(state.duration);
      els.videoDims.textContent =
        info.width && info.height ? `${info.width}×${info.height}` : "unknown";
      els.thumbImg.src = info.thumbnail || "";

      buildQualityChips(info.resolutions || [], info.has_audio_only);

      els.cardPreview.classList.remove("hidden");
      els.cardTimeline.classList.remove("hidden");
      els.cardOutput.classList.remove("hidden");

      resetCrop();
      initTimeline();
      updateRenderEnabled();
    } catch (err) {
      showError(els.urlError, err.message || "Couldn't load that video.");
    } finally {
      els.loadBtn.disabled = false;
      els.loadBtn.querySelector(".btn-label").textContent = "Load";
      els.loadBtn.querySelector(".btn-spinner").hidden = true;
    }
  }

  // ---- Crop box -----------------------------------------------------------
  function resetCrop() {
    state.cropEnabled = false;
    state.crop = null;
    els.cropBox.classList.remove("active");
    els.cropToggle.classList.remove("active");
    els.cropToggle.textContent = "Enable crop";
    els.cropReadout.hidden = true;
  }

  function toggleCrop() {
    state.cropEnabled = !state.cropEnabled;
    els.cropToggle.classList.toggle("active", state.cropEnabled);
    els.cropToggle.textContent = state.cropEnabled ? "Disable crop" : "Enable crop";
    els.cropBox.classList.toggle("active", state.cropEnabled);
    els.cropReadout.hidden = !state.cropEnabled;

    if (state.cropEnabled && !state.crop) {
      const wrapRect = els.thumbWrap.getBoundingClientRect();
      const w = wrapRect.width * 0.6;
      const h = wrapRect.height * 0.6;
      const x = (wrapRect.width - w) / 2;
      const y = (wrapRect.height - h) / 2;
      setCropBoxPx(x, y, w, h);
    }
    updateCropReadout();
  }

  function setCropBoxPx(x, y, w, h) {
    els.cropBox.style.left = `${x}px`;
    els.cropBox.style.top = `${y}px`;
    els.cropBox.style.width = `${w}px`;
    els.cropBox.style.height = `${h}px`;
  }

  function updateCropReadout() {
    if (!state.cropEnabled) return;
    const rect = els.thumbWrap.getBoundingClientRect();
    const boxRect = els.cropBox.getBoundingClientRect();
    const scaleX = (state.nativeWidth || rect.width) / rect.width;
    const scaleY = (state.nativeHeight || rect.height) / rect.height;

    const x = Math.round((boxRect.left - rect.left) * scaleX);
    const y = Math.round((boxRect.top - rect.top) * scaleY);
    const w = Math.round(boxRect.width * scaleX);
    const h = Math.round(boxRect.height * scaleY);

    state.crop = { x: Math.max(0, x), y: Math.max(0, y), width: Math.max(2, w), height: Math.max(2, h) };
    els.cropReadout.textContent = `x${state.crop.x} y${state.crop.y} · w${state.crop.width} h${state.crop.height}`;
  }

  function makeCropDraggable() {
    let mode = null; // 'move' | handle name
    let start = null;

    function onDown(e, m) {
      mode = m;
      const point = e.touches ? e.touches[0] : e;
      const boxRect = els.cropBox.getBoundingClientRect();
      start = {
        x: point.clientX,
        y: point.clientY,
        left: boxRect.left,
        top: boxRect.top,
        width: boxRect.width,
        height: boxRect.height,
      };
      e.preventDefault();
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onUp);
    }

    function onMove(e) {
      if (!mode) return;
      const point = e.touches ? e.touches[0] : e;
      const dx = point.clientX - start.x;
      const dy = point.clientY - start.y;
      const wrapRect = els.thumbWrap.getBoundingClientRect();

      let left = start.left - wrapRect.left;
      let top = start.top - wrapRect.top;
      let width = start.width;
      let height = start.height;

      if (mode === "move") {
        left += dx;
        top += dy;
      } else {
        if (mode.includes("e")) width += dx;
        if (mode.includes("s")) height += dy;
        if (mode.includes("w")) { width -= dx; left += dx; }
        if (mode.includes("n")) { height -= dy; top += dy; }
      }

      width = Math.max(20, Math.min(width, wrapRect.width - left));
      height = Math.max(20, Math.min(height, wrapRect.height - top));
      left = Math.max(0, Math.min(left, wrapRect.width - width));
      top = Math.max(0, Math.min(top, wrapRect.height - height));

      setCropBoxPx(left, top, width, height);
      updateCropReadout();
      e.preventDefault();
    }

    function onUp() {
      mode = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
    }

    els.cropBox.addEventListener("mousedown", (e) => {
      if (e.target.classList.contains("crop-handle")) return;
      onDown(e, "move");
    });
    els.cropBox.addEventListener("touchstart", (e) => {
      if (e.target.classList.contains("crop-handle")) return;
      onDown(e, "move");
    }, { passive: false });

    els.cropBox.querySelectorAll(".crop-handle").forEach((h) => {
      h.addEventListener("mousedown", (e) => onDown(e, h.dataset.handle));
      h.addEventListener("touchstart", (e) => onDown(e, h.dataset.handle), { passive: false });
    });
  }

  // ---- Timeline scrubber (signature element) -------------------------------
  function initTimeline() {
    updateTimelineUI();
  }

  function updateTimelineUI() {
    const dur = state.duration || 1;
    const trackWidth = els.track.clientWidth;
    const startPx = (state.clipStart / dur) * trackWidth;
    const endPx = (state.clipEnd / dur) * trackWidth;

    els.handleIn.style.left = `${startPx}px`;
    els.handleOut.style.left = `${endPx}px`;
    els.rangeFill.style.left = `${startPx}px`;
    els.rangeFill.style.width = `${Math.max(0, endPx - startPx)}px`;

    els.tcIn.textContent = fmtTime(state.clipStart);
    els.tcOut.textContent = fmtTime(state.clipEnd);
    els.tcClipDuration.textContent = fmtTime(state.clipEnd - state.clipStart);

    els.handleIn.setAttribute("aria-valuenow", state.clipStart.toFixed(1));
    els.handleOut.setAttribute("aria-valuenow", state.clipEnd.toFixed(1));
  }

  function makeTimelineDraggable() {
    function pxToSeconds(px) {
      const trackWidth = els.track.clientWidth;
      const ratio = Math.min(1, Math.max(0, px / trackWidth));
      return ratio * (state.duration || 0);
    }

    function attach(handle, isStart) {
      function onDown(e) {
        e.preventDefault();
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        window.addEventListener("touchmove", onMove, { passive: false });
        window.addEventListener("touchend", onUp);
      }
      function onMove(e) {
        const point = e.touches ? e.touches[0] : e;
        const trackRect = els.track.getBoundingClientRect();
        const px = point.clientX - trackRect.left;
        const seconds = pxToSeconds(px);

        if (isStart) {
          state.clipStart = Math.min(seconds, state.clipEnd - 0.2);
          state.clipStart = Math.max(0, state.clipStart);
        } else {
          state.clipEnd = Math.max(seconds, state.clipStart + 0.2);
          state.clipEnd = Math.min(state.duration, state.clipEnd);
        }
        updateTimelineUI();
        e.preventDefault();
      }
      function onUp() {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        window.removeEventListener("touchmove", onMove);
        window.removeEventListener("touchend", onUp);
      }
      handle.addEventListener("mousedown", onDown);
      handle.addEventListener("touchstart", onDown, { passive: false });

      handle.addEventListener("keydown", (e) => {
        const step = e.shiftKey ? 5 : 1;
        if (e.key === "ArrowLeft") {
          if (isStart) state.clipStart = Math.max(0, state.clipStart - step);
          else state.clipEnd = Math.max(state.clipStart + 0.2, state.clipEnd - step);
          updateTimelineUI();
        } else if (e.key === "ArrowRight") {
          if (isStart) state.clipStart = Math.min(state.clipEnd - 0.2, state.clipStart + step);
          else state.clipEnd = Math.min(state.duration, state.clipEnd + step);
          updateTimelineUI();
        }
      });
    }

    attach(els.handleIn, true);
    attach(els.handleOut, false);
  }

  // ---- Output settings ----------------------------------------------------
  function buildQualityChips(resolutions, hasAudioOnly) {
    els.qualityChips.innerHTML = "";
    const options = [{ label: "Best available", value: "best" }, ...resolutions];
    if (hasAudioOnly) options.push({ label: "Audio only", value: "audio" });

    options.forEach((opt, idx) => {
      const btn = document.createElement("button");
      btn.className = "chip" + (idx === 0 ? " active" : "");
      btn.textContent = opt.label;
      btn.dataset.value = opt.value;
      btn.addEventListener("click", () => {
        els.qualityChips.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        state.quality = opt.value;
      });
      els.qualityChips.appendChild(btn);
    });
    state.quality = options[0].value;
  }

  function initContainerChips() {
    els.containerChips.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        els.containerChips.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.container = chip.dataset.container;
      });
    });
  }

  function updateRenderEnabled() {
    els.renderBtn.disabled = !els.confirmPermission.checked;
  }

  // ---- Step 5: render + poll -----------------------------------------------
  async function renderClip() {
    clearError(els.processError);
    els.renderBtn.disabled = true;

    const audioOnly = state.container === "mp3" || state.container === "m4a" || state.quality === "audio";
    const payload = {
      url: state.url,
      start_time: Number(state.clipStart.toFixed(2)),
      end_time: Number(state.clipEnd.toFixed(2)),
      crop: state.cropEnabled && state.crop ? state.crop : null,
      container: state.container,
      quality: state.quality,
      audio_only: audioOnly,
      confirm_permission: els.confirmPermission.checked,
    };

    try {
      const { job_id: jobId } = await apiPost(API.process, payload);
      els.cardProgress.classList.remove("hidden");
      els.cardProgress.scrollIntoView({ behavior: "smooth", block: "nearest" });
      els.resultRow.classList.add("hidden");
      els.progressHeading.textContent = "Processing…";
      pollStatus(jobId);
    } catch (err) {
      showError(els.processError, err.message || "Couldn't start the job.");
      els.renderBtn.disabled = false;
    }
  }

  function pollStatus(jobId) {
    if (state.pollTimer) clearInterval(state.pollTimer);

    state.pollTimer = setInterval(async () => {
      try {
        const res = await fetch(API.status(jobId));
        const job = await res.json();

        els.tapeFill.style.width = `${job.progress}%`;
        els.progressMessage.textContent = job.message || job.status;

        if (job.status === "completed") {
          clearInterval(state.pollTimer);
          els.progressHeading.textContent = "Clip ready";
          els.resultRow.classList.remove("hidden");
          els.resultName.textContent = job.output_name || "clip";
          els.resultSize.textContent = fmtBytes(job.output_size_bytes);
          els.downloadLink.href = API.download(jobId);
          els.downloadLink.setAttribute("download", job.output_name || "clip");
          els.renderBtn.disabled = false;
        } else if (job.status === "failed") {
          clearInterval(state.pollTimer);
          els.progressHeading.textContent = "Something went wrong";
          els.progressMessage.textContent = job.error || "The job failed.";
          els.renderBtn.disabled = false;
        }
      } catch (err) {
        // transient network hiccup — keep polling
      }
    }, 1200);
  }

  // ---- Wire up --------------------------------------------------------------
  els.loadBtn.addEventListener("click", loadVideo);
  els.urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadVideo();
  });
  els.cropToggle.addEventListener("click", toggleCrop);
  els.confirmPermission.addEventListener("change", updateRenderEnabled);
  els.renderBtn.addEventListener("click", renderClip);

  window.addEventListener("resize", () => {
    if (state.duration) updateTimelineUI();
  });

  makeCropDraggable();
  makeTimelineDraggable();
  initContainerChips();
})();
