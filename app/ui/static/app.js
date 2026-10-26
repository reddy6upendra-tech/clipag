// AI Reels Clipper Client App - Studio Edition
let activeTab = "url";
let currentJobId = null;
let pollTimer = null;
let currentContentHash = null;
let currentClips = [];

document.addEventListener("DOMContentLoaded", () => {
  initDropZone();
  loadSystemStatus();
  updateRecentJobsCount();

  // Check if URL has ?job=<job_id>
  const urlParams = new URLSearchParams(window.location.search);
  const paramJobId = urlParams.get("job");
  if (paramJobId) {
    currentJobId = paramJobId;
    document.getElementById("progressSection").classList.remove("hidden");
    document.getElementById("startBtn").disabled = true;
    updateProgress(10, "Reconnecting to job...", "ingest");
    pollJobStatus(currentJobId);
  }
});

function switchTab(tab) {
  activeTab = tab;
  document.getElementById("tabUrlBtn").classList.toggle("active", tab === "url");
  document.getElementById("tabFileBtn").classList.toggle("active", tab === "file");
  document.getElementById("urlContent").classList.toggle("hidden", tab !== "url");
  document.getElementById("fileContent").classList.toggle("hidden", tab !== "file");
}

function initDropZone() {
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");

  ["dragenter", "dragover"].forEach(event => {
    dropZone.addEventListener(event, (e) => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach(event => {
    dropZone.addEventListener(event, (e) => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });

  dropZone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      fileInput.files = e.dataTransfer.files;
      handleFileSelected(fileInput.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length > 0) {
      handleFileSelected(fileInput.files[0]);
    }
  });
}

function handleFileSelected(file) {
  document.getElementById("dropText").innerHTML = `Selected: <span>${file.name}</span> (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
  document.getElementById("dropSub").innerText = "Click to select a different file";
}

async function loadSystemStatus() {
  try {
    const res = await fetch("/api/doctor");
    if (res.ok) {
      const data = await res.json();
      const dot = document.querySelector(".status-dot");
      const text = document.getElementById("statusText");
      if (data.ffmpeg) {
        dot.classList.add("active");
        text.innerText = data.gemini ? "System Ready (Gemini Cloud + CPU)" : "System Ready (Heuristic Fallback + CPU)";
      } else {
        text.innerText = "FFmpeg missing! Please check README";
      }
    }
  } catch (err) {
    console.warn("Could not check doctor status:", err);
  }
}

async function startGeneration() {
  dismissError();
  const startBtn = document.getElementById("startBtn");
  const urlVal = document.getElementById("videoUrl").value.trim();
  const fileVal = document.getElementById("fileInput").files[0];

  if (activeTab === "url" && !urlVal) {
    showError("Please enter a YouTube or video URL.", "ingest");
    return;
  }
  if (activeTab === "file" && !fileVal) {
    showError("Please select a video file to upload.", "ingest");
    return;
  }

  const formData = new FormData();
  if (activeTab === "url") {
    formData.append("url", urlVal);
  } else {
    formData.append("file", fileVal);
  }

  const numClips = document.getElementById("numClips").value;
  formData.append("clips", numClips);
  formData.append("style", document.getElementById("captionStyle").value);
  formData.append("model", document.getElementById("whisperModel").value);
  formData.append("language", document.getElementById("languageSelect").value);
  formData.append("gemini_model", document.getElementById("geminiModel").value);
  formData.append("position", document.getElementById("captionPosition").value);
  formData.append("font_size", document.getElementById("captionFontSize").value);

  // Show progress section
  document.getElementById("progressSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.add("hidden");
  updateProgress(2, "Submitting job to queue...", "queue");
  startBtn.disabled = true;

  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "Failed to submit job");
    }

    const data = await res.json();
    currentJobId = data.job_id;

    // Update URL query string without reloading page
    window.history.replaceState({}, "", `?job=${currentJobId}`);

    // Save to local storage
    saveJobToHistory(currentJobId, activeTab === "url" ? urlVal : fileVal.name, "Processing");

    pollJobStatus(currentJobId);
  } catch (err) {
    showError(err.message, "ingest");
    startBtn.disabled = false;
    document.getElementById("progressSection").classList.add("hidden");
  }
}

function pollJobStatus(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) {
        if (res.status === 404) {
          throw new Error("Job not found or expired. Jobs are retained for 24 hours.");
        }
        throw new Error("Failed to check job progress");
      }
      const job = await res.json();

      // Handle Queued State
      const queueBanner = document.getElementById("queueBanner");
      if (job.status === "queued") {
        queueBanner.classList.remove("hidden");
        document.getElementById("queueTitle").innerText = `Queued (Position #${job.queue_position || 1})`;
        updateProgress(0, job.step || "Waiting in queue...", "queue");
        return;
      } else {
        queueBanner.classList.add("hidden");
      }

      updateProgress(job.progress, job.step, job.active_stage);

      if (job.status === "completed") {
        clearInterval(pollTimer);
        document.getElementById("startBtn").disabled = false;
        document.getElementById("progressSection").classList.add("hidden");
        saveJobToHistory(jobId, null, "Completed");
        displayResults(job.result);
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        document.getElementById("startBtn").disabled = false;
        document.getElementById("progressSection").classList.add("hidden");
        saveJobToHistory(jobId, null, "Failed");
        showError(job.error || "Processing failed. Please check logs.", job.error_stage);
      }
    } catch (err) {
      console.error("Polling error:", err);
      clearInterval(pollTimer);
      document.getElementById("startBtn").disabled = false;
      showError(err.message, "general");
    }
  }, 1200);
}

function updateProgress(pct, stepMsg, stage) {
  const p = Math.max(0, Math.min(100, pct || 0));
  document.getElementById("progressPct").innerText = `${p}%`;
  document.getElementById("progressBarFill").style.width = `${p}%`;
  document.getElementById("progressDetails").innerText = stepMsg || "Working...";

  // Update Stepper
  const stages = ["ingest", "transcribe", "select", "render", "package"];
  let activeIdx = stages.indexOf(stage);
  if (stage === "complete") activeIdx = 5;

  stages.forEach((st, idx) => {
    const node = document.getElementById(`step-node-${st}`);
    const line = document.getElementById(`step-line-${idx}`);
    if (node) {
      if (idx < activeIdx) {
        node.className = "step-node completed";
      } else if (idx === activeIdx) {
        node.className = "step-node active";
      } else {
        node.className = "step-node";
      }
    }
    if (line) {
      line.className = idx < activeIdx ? "step-line completed" : "step-line";
    }
  });
}

function showError(msg, stage) {
  document.getElementById("errorMsg").innerText = msg;
  const troubleshooter = document.getElementById("errorTroubleshoot");
  troubleshooter.innerHTML = "";

  const lower = (msg || "").toLowerCase();
  let advice = "";
  if (lower.includes("private")) {
    advice = "💡 <strong>Tip:</strong> This YouTube video is private. Please make it Unlisted or Public in YouTube Studio, or download the MP4 and upload it via the 'Upload Local Video' tab.";
  } else if (lower.includes("age-restricted") || lower.includes("sign in")) {
    advice = "💡 <strong>Tip:</strong> This YouTube video is age-restricted. YouTube blocks bot downloads for age-gated videos. Please download the video locally and upload it directly.";
  } else if (lower.includes("playlist")) {
    advice = "💡 <strong>Tip:</strong> Provide a direct link to a single video instead of a playlist.";
  } else if (lower.includes("no speech")) {
    advice = "💡 <strong>Tip:</strong> No dialogue or speech was detected in the video audio. Ensure the video contains spoken commentary or podcast dialogue.";
  } else if (lower.includes("too short")) {
    advice = "💡 <strong>Tip:</strong> Videos must be at least 15 seconds long so our AI can extract 20–60s reels.";
  } else if (stage === "transcribe") {
    advice = "💡 <strong>Tip:</strong> If transcribing non-English podcasts (Telugu/Hindi), make sure to select the language explicitly from the dropdown and use the 'small' model.";
  }

  if (advice) {
    troubleshooter.innerHTML = advice;
  }

  document.getElementById("errorSection").classList.remove("hidden");
  document.getElementById("errorSection").scrollIntoView({ behavior: "smooth" });
}

function dismissError() {
  document.getElementById("errorSection").classList.add("hidden");
}

function copyJobShareLink() {
  const url = window.location.origin + window.location.pathname + `?job=${currentJobId}`;
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.getElementById("copyJobLinkBtn");
    const orig = btn.innerText;
    btn.innerText = "✅ Link Copied!";
    btn.style.background = "#10b981";
    setTimeout(() => {
      btn.innerText = orig;
      btn.style.background = "";
    }, 2500);
  }).catch(() => {
    prompt("Copy this direct link to return to your job later:", url);
  });
}

async function saveNotification() {
  const contact = document.getElementById("notifyContactInput").value.trim();
  const statusEl = document.getElementById("notifyStatus");
  if (!contact) {
    alert("Please enter an email address or WhatsApp phone number.");
    return;
  }
  if (!currentJobId) return;

  try {
    const res = await fetch(`/api/jobs/${currentJobId}/notify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contact }),
    });
    if (res.ok) {
      statusEl.classList.remove("hidden");
      statusEl.innerHTML = `✅ Saved! We will ping <strong>${contact}</strong> as soon as your reels finish.`;
    }
  } catch (err) {
    console.error("Failed to register notification:", err);
  }
}

function formatDuration(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function displayResults(result) {
  if (!result || !result.clips) return;
  currentContentHash = result.content_hash_id;
  currentClips = result.clips;
  const grid = document.getElementById("reelsGrid");
  grid.innerHTML = "";

  const langLabel = result.language ? ` (${result.language.toUpperCase()})` : '';
  document.getElementById("resultsSummary").innerText = 
    `${result.clips.length} vertical 9:16 reels${langLabel} ready for Instagram & YouTube Shorts`;

  document.getElementById("downloadAllBtn").href = result.zip_url;

  result.clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "reel-card";
    card.id = `reel-card-${clip.clip_index}`;

    const hashtagsHtml = (clip.hashtags || [])
      .map(tag => `<span class="hashtag-pill">${tag.startsWith('#') ? tag : '#' + tag}</span>`)
      .join(" ");

    card.innerHTML = `
      <div class="reel-video-wrapper">
        <video id="video-player-${clip.clip_index}" controls playsinline preload="metadata">
          <source src="${clip.video_url}" type="video/mp4">
          Your browser does not support HTML5 video.
        </video>
        <div class="reel-score-badge">🔥 Virality: ${clip.score || 85}/100</div>
      </div>
      <div class="reel-body">
        <div class="reel-time" id="time-display-${clip.clip_index}">
          ⏱️ [${formatDuration(clip.start_sec)} - ${formatDuration(clip.end_sec)}] (${clip.duration_sec.toFixed(1)}s)
        </div>
        <h3 class="reel-title">${clip.title}</h3>
        <p class="reel-hook">🎣 <strong>Hook:</strong> "${clip.hook_line}"</p>
        <div class="reel-reason">💡 <strong>Why it works:</strong> ${clip.reason}</div>
        <div class="reel-hashtags">${hashtagsHtml}</div>
        
        <div class="reel-actions">
          <div class="action-btn-row">
            <a href="${clip.video_url}" download="${clip.filename}" class="btn-download-clip">
              ⬇️ Download Reel (.mp4)
            </a>
            <button class="btn-copy-meta" id="copy-btn-${clip.clip_index}" onclick="copyReelMetadata(${clip.clip_index})">
              📋 Copy Caption
            </button>
          </div>

          <!-- Quick Interactive Trimmer & Swap -->
          <div class="adjust-box">
            <div class="adjust-header">
              <span class="adjust-title">⏱️ Quick Trim & Re-render</span>
              <button class="btn-swap-clip" id="swap-btn-${clip.clip_index}" onclick="swapClipCandidate(${clip.clip_index})">
                🔄 Swap Moment
              </button>
            </div>
            
            <div class="trim-inputs-row">
              <div class="trim-col">
                <label>Start (s):</label>
                <div class="trim-stepper">
                  <button type="button" class="btn-mini" onclick="shiftTrimInput(${clip.clip_index}, 'start', -2)">-2s</button>
                  <input type="number" step="0.5" id="trim-start-${clip.clip_index}" value="${clip.start_sec.toFixed(1)}">
                  <button type="button" class="btn-mini" onclick="shiftTrimInput(${clip.clip_index}, 'start', 2)">+2s</button>
                </div>
              </div>
              <div class="trim-col">
                <label>End (s):</label>
                <div class="trim-stepper">
                  <button type="button" class="btn-mini" onclick="shiftTrimInput(${clip.clip_index}, 'end', -2)">-2s</button>
                  <input type="number" step="0.5" id="trim-end-${clip.clip_index}" value="${clip.end_sec.toFixed(1)}">
                  <button type="button" class="btn-mini" onclick="shiftTrimInput(${clip.clip_index}, 'end', 2)">+2s</button>
                </div>
              </div>
            </div>

            <button class="btn-rerender" id="rerender-btn-${clip.clip_index}" onclick="reRenderClipCustom(${clip.clip_index})">
              ⚡ Apply & Re-render This Clip
            </button>
          </div>
        </div>
      </div>
    `;

    grid.appendChild(card);
  });

  document.getElementById("resultsSection").classList.remove("hidden");
  document.getElementById("resultsSection").scrollIntoView({ behavior: "smooth" });
}

function shiftTrimInput(clipIndex, field, delta) {
  const el = document.getElementById(`trim-${field}-${clipIndex}`);
  if (el) {
    let cur = parseFloat(el.value) || 0;
    el.value = Math.max(0, cur + delta).toFixed(1);
  }
}

function copyReelMetadata(clipIndex) {
  const clip = currentClips.find(c => c.clip_index === clipIndex);
  if (!clip) return;
  const tags = (clip.hashtags || []).map(t => t.startsWith('#') ? t : '#' + t).join(' ');
  const text = `${clip.title}\n\n"${clip.hook_line}"\n\n${clip.description}\n\n${tags}`;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById(`copy-btn-${clipIndex}`);
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = "✅ Copied!";
      btn.style.background = "#10b981";
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.style.background = "";
      }, 2500);
    }
  }).catch(() => {
    prompt("Copy reel metadata:", text);
  });
}

async function reRenderClipCustom(clipIndex) {
  const btn = document.getElementById(`rerender-btn-${clipIndex}`);
  if (!currentContentHash) return;

  const startVal = parseFloat(document.getElementById(`trim-start-${clipIndex}`).value);
  const endVal = parseFloat(document.getElementById(`trim-end-${clipIndex}`).value);

  if (isNaN(startVal) || isNaN(endVal) || endVal <= startVal) {
    alert("Please enter a valid Start and End time (End must be greater than Start).");
    return;
  }

  const style = document.getElementById("captionStyle").value;
  const position = document.getElementById("captionPosition").value;
  const fontSize = parseInt(document.getElementById("captionFontSize").value) || 78;

  btn.disabled = true;
  btn.innerText = "⏳ Re-rendering clip on CPU...";

  try {
    const res = await fetch(`/api/jobs/${currentContentHash}/adjust`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clip_index: clipIndex,
        start_time: startVal,
        end_time: endVal,
        caption_style: style,
        caption_position: position,
        font_size: fontSize,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to re-render clip");
    }

    const updated = await res.json();

    // Update in-memory clips
    const cIdx = currentClips.findIndex(c => c.clip_index === clipIndex);
    if (cIdx >= 0) {
      currentClips[cIdx] = { ...currentClips[cIdx], ...updated };
    }

    // Reload video player with fresh timestamp
    const player = document.getElementById(`video-player-${clipIndex}`);
    player.src = updated.video_url + `&r=${Date.now()}`;
    player.load();

    document.getElementById(`time-display-${clipIndex}`).innerText = 
      `⏱️ [${formatDuration(updated.start_sec)} - ${formatDuration(updated.end_sec)}] (${updated.duration_sec.toFixed(1)}s)`;

    btn.innerText = "✅ Updated!";
    btn.style.background = "#10b981";
    setTimeout(() => {
      btn.disabled = false;
      btn.innerText = "⚡ Apply & Re-render This Clip";
      btn.style.background = "#2563eb";
    }, 2500);

  } catch (err) {
    alert("Re-render failed: " + err.message);
    btn.disabled = false;
    btn.innerText = "⚡ Try Re-render Again";
  }
}

async function swapClipCandidate(clipIndex) {
  if (!confirm(`Swap Clip #${clipIndex} with an alternative moment from this video?`)) return;
  const btn = document.getElementById(`swap-btn-${clipIndex}`);
  btn.disabled = true;
  btn.innerText = "Finding alternative...";

  try {
    const style = document.getElementById("captionStyle").value;
    const res = await fetch(`/api/jobs/${currentContentHash}/swap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clip_index: clipIndex,
        caption_style: style,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Could not find a suitable alternative clip.");
    }

    const updated = await res.json();
    location.reload();
  } catch (err) {
    alert("Swap failed: " + err.message);
    btn.disabled = false;
    btn.innerText = "🔄 Swap Moment";
  }
}

// LocalStorage History Helpers
function saveJobToHistory(jobId, title, status) {
  try {
    let history = JSON.parse(localStorage.getItem("clipag_recent_jobs") || "[]");
    const existing = history.find(j => j.id === jobId);
    if (existing) {
      if (status) existing.status = status;
    } else {
      history.unshift({
        id: jobId,
        title: title || `Job ${jobId}`,
        status: status || "Processing",
        timestamp: new Date().toISOString(),
      });
    }
    history = history.slice(0, 10); // Keep last 10
    localStorage.setItem("clipag_recent_jobs", JSON.stringify(history));
    updateRecentJobsCount();
  } catch (e) {
    console.warn("Storage error:", e);
  }
}

function updateRecentJobsCount() {
  try {
    const history = JSON.parse(localStorage.getItem("clipag_recent_jobs") || "[]");
    const el = document.getElementById("recentJobsCount");
    if (el) el.innerText = history.length;
  } catch (e) {}
}

function toggleRecentJobsModal() {
  const modal = document.getElementById("recentJobsModal");
  if (modal.classList.contains("hidden")) {
    renderRecentJobsList();
    modal.classList.remove("hidden");
  } else {
    modal.classList.add("hidden");
  }
}

function renderRecentJobsList() {
  const container = document.getElementById("recentJobsList");
  try {
    const history = JSON.parse(localStorage.getItem("clipag_recent_jobs") || "[]");
    if (history.length === 0) {
      container.innerHTML = '<p class="empty-hint">No saved jobs found on this browser yet.</p>';
      return;
    }
    container.innerHTML = history.map(item => `
      <div class="job-history-item">
        <div>
          <strong>${item.title.length > 40 ? item.title.slice(0, 40) + '...' : item.title}</strong>
          <div class="job-item-sub">ID: ${item.id} • ${new Date(item.timestamp).toLocaleString()}</div>
        </div>
        <div class="job-item-actions">
          <span class="job-status-badge ${item.status.toLowerCase()}">${item.status}</span>
          <a href="?job=${item.id}" class="btn-resume">Open</a>
        </div>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = '<p class="empty-hint">Error reading history.</p>';
  }
}
