/* ─── MH-Downsample Pro — Extension Popup Logic ─── */

const API = "http://127.0.0.1:8765";

/* ─── DOM REFERENCES ─── */

const serverStatus = document.querySelector("#server-status");
const serverText = document.querySelector("#server-text");
const statusBadge = document.querySelector("#status-badge");
const urlInput = document.querySelector("#url-input");
const btnDownload = document.querySelector("#btn-download");
const btnText = document.querySelector("#btn-text");
const btnOpen = document.querySelector("#btn-open");
const progressSection = document.querySelector("#progress-section");
const progressBadge = document.querySelector("#progress-badge");
const counter = document.querySelector("#counter");
const progressBar = document.querySelector("#progress-bar");
const progressPercent = document.querySelector("#progress-percent");
const currentFile = document.querySelector("#current-file");
const checkLink = document.querySelector("#check-link");
const checkDiscover = document.querySelector("#check-discover");
const checkDownload = document.querySelector("#check-download");
const checkDone = document.querySelector("#check-done");
const resultsSection = document.querySelector("#results-section");
const statDiscovered = document.querySelector("#stat-discovered");
const statDownloaded = document.querySelector("#stat-downloaded");
const statFailed = document.querySelector("#stat-failed");
const resultDir = document.querySelector("#result-dir");
const emptyState = document.querySelector("#empty-state");
const message = document.querySelector("#message");
const brandLogo = document.querySelector("#brand-logo");

/* ─── STATE ─── */

let pollTimer = null;
let activeJobId = null;

/* ─── HELPERS ─── */

/**
 * Display a message below the main content.
 * @param {string} text - Message text to show. Empty string clears.
 * @param {string} kind - Optional: "error" or "success" for styling.
 */
function showMessage(text, kind) {
  message.textContent = text;
  message.className = "message" + (kind ? " message--" + kind : "");
}

function setWorkingState(working) {
  brandLogo.classList.toggle("active", working);
  btnDownload.classList.toggle("loading", working);
}

/**
 * Make an HTTP request to the local backend API.
 * @param {string} path - API path (e.g. "/health").
 * @param {object} options - Optional fetch options (method, body, etc.).
 * @returns {Promise<object>} Parsed JSON response.
 */
async function request(path, options) {
  const response = await fetch(API + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options && options.headers ? options.headers : {})
    }
  });
  const payload = await response.json().catch(function () {
    return {};
  });
  if (!response.ok) {
    throw new Error(payload.error || "L\u1ed7i HTTP " + response.status);
  }
  return payload;
}

/**
 * Set a single checklist icon to a specific state.
 * @param {HTMLElement} icon - The check-icon element.
 * @param {"done"|"active"|"pending"|"error"} state - Visual state.
 */
function setCheckState(icon, state) {
  switch (state) {
    case "done":
      icon.className = "check-icon check-icon--done";
      icon.textContent = "\u2713";
      break;
    case "active":
      icon.className = "check-icon check-icon--active";
      icon.textContent = "\u25C9";
      break;
    case "error":
      icon.className = "check-icon check-icon--error";
      icon.textContent = "\u2717";
      break;
    default:
      icon.className = "check-icon check-icon--pending";
      icon.textContent = "\u25CB";
      break;
  }
}

/**
 * Update the badge element with text and optional variant.
 * @param {HTMLElement} badge - Badge span element.
 * @param {string} text - Badge text content.
 * @param {string} variant - Optional CSS class suffix: "ready", "error", "processing".
 */
function setBadge(badge, text, variant) {
  badge.textContent = text;
  badge.className = "badge" + (variant ? " badge--" + variant : "");
}

/**
 * Reset all checklist icons to pending state.
 */
function resetChecklist() {
  setCheckState(checkLink, "pending");
  setCheckState(checkDiscover, "pending");
  setCheckState(checkDownload, "pending");
  setCheckState(checkDone, "pending");
}

/* ─── SERVER CHECK ─── */

/**
 * Check if the local backend server is running.
 * Updates the status bar, badge, and button state.
 */
async function checkServer() {
  try {
    var health = await request("/health");
    serverStatus.classList.add("online");
    serverText.textContent = "Server k\u1ebft n\u1ed1i \u00b7 " + health.download_root;
    btnDownload.disabled = false;
    setWorkingState(false);
    setBadge(statusBadge, "TR\u1ea0NG TH\u00c1I S\u1eb4N S\u00c0NG", "ready");
  } catch (err) {
    serverStatus.classList.remove("online");
    serverText.textContent = "Server ngo\u1ea1i tuy\u1ebfn";
    btnDownload.disabled = true;
    setWorkingState(false);
    setBadge(statusBadge, "KH\u00d4NG K\u1ebeT N\u1ed0I", "error");
    showMessage("H\u00e3y m\u1edf file START-SERVER.cmd trong th\u01b0 m\u1ee5c d\u1ef1 \u00e1n.", "error");
  }
}

/* ─── CHECKLIST UPDATER ─── */

/**
 * Update the 4-step checklist based on current job status.
 * @param {object} job - Job object from the backend API.
 */
function updateChecklist(job) {
  switch (job.status) {
    case "queued":
      setCheckState(checkLink, "done");
      setCheckState(checkDiscover, "pending");
      setCheckState(checkDownload, "pending");
      setCheckState(checkDone, "pending");
      break;

    case "discovering":
      setCheckState(checkLink, "done");
      setCheckState(checkDiscover, "active");
      setCheckState(checkDownload, "pending");
      setCheckState(checkDone, "pending");
      break;

    case "downloading":
      setCheckState(checkLink, "done");
      setCheckState(checkDiscover, "done");
      setCheckState(checkDownload, "active");
      setCheckState(checkDone, "pending");
      break;

    case "completed":
      setCheckState(checkLink, "done");
      setCheckState(checkDiscover, "done");
      setCheckState(checkDownload, "done");
      setCheckState(checkDone, "done");
      break;

    case "failed":
      setCheckState(checkLink, "done");
      if (job.discovered === 0) {
        setCheckState(checkDiscover, "error");
        setCheckState(checkDownload, "pending");
      } else {
        setCheckState(checkDiscover, "done");
        setCheckState(checkDownload, "error");
      }
      setCheckState(checkDone, "pending");
      break;

    default:
      break;
  }
}

/* ─── RENDER ─── */

/**
 * Render the current job state into the popup UI.
 * Manages visibility of progress, results, and empty states.
 * @param {object} job - Job object from the backend API.
 * @returns {boolean} True if the job has finished (completed or failed).
 */
function render(job) {
  var total = Number(job.discovered) || 0;
  var done = (Number(job.downloaded) || 0) + (Number(job.failed) || 0);
  var percent = total > 0 ? Math.min(100, Math.round(done * 100 / total)) : 0;
  var finished = job.status === "completed" || job.status === "failed";

  /* --- Status labels --- */
  var statusLabels = {
    queued: "X\u1ebeP H\u00c0NG",
    discovering: "\u0110ANG T\u00ccM KI\u1ebeM",
    downloading: "\u0110ANG T\u1ea2I",
    completed: "HO\u00c0N T\u1ea4T",
    failed: "TH\u1ea4T B\u1ea0I"
  };

  /* --- Completed --- */
  if (job.status === "completed") {
    progressSection.hidden = true;
    emptyState.hidden = true;

    resultsSection.hidden = false;
    resultsSection.classList.add("fade-in-up");

    statDiscovered.textContent = String(job.discovered);
    statDownloaded.textContent = String(job.downloaded);
    statFailed.textContent = String(job.failed);

    if (job.output_dir) {
      resultDir.textContent = job.output_dir;
      resultDir.title = job.output_dir;
      resultDir.hidden = false;
    } else {
      resultDir.hidden = true;
    }

    btnOpen.hidden = !job.output_dir;
    setBadge(statusBadge, "HO\u00c0N T\u1ea4T", "ready");

    var successMsg = "\u0110\u00e3 t\u1ea3i " + job.downloaded + " file g\u1ed1c";
    if (job.failed > 0) {
      successMsg += " \u00b7 " + job.failed + " l\u1ed7i";
    }
    showMessage(successMsg, "success");

    btnDownload.disabled = false;
    setWorkingState(false);
    btnText.textContent = "Qu\u00e9t v\u00e0 t\u1ea3i \u00e2m thanh";
    return true;
  }

  /* --- Failed --- */
  if (job.status === "failed") {
    if (job.discovered === 0 && job.error && job.error.indexOf("Kh\u00f4ng t\u00ecm th\u1ea5y") !== -1) {
      progressSection.hidden = true;
      resultsSection.hidden = true;
      emptyState.hidden = false;
      emptyState.classList.add("fade-in-up");
    } else {
      progressSection.hidden = false;
      resultsSection.hidden = true;
      emptyState.hidden = true;

      setBadge(progressBadge, statusLabels.failed || job.status, "error");
      counter.textContent = total > 0 ? job.downloaded + "/" + total : "0/0";
      progressBar.style.width = percent + "%";
      progressPercent.textContent = percent + "%";
      currentFile.textContent = "";
      updateChecklist(job);
    }

    showMessage(job.error || "Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c file.", "error");
    setBadge(statusBadge, "L\u1ed6I", "error");
    btnDownload.disabled = false;
    setWorkingState(false);
    btnText.textContent = "Qu\u00e9t v\u00e0 t\u1ea3i \u00e2m thanh";
    return true;
  }

  /* --- In progress (queued / discovering / downloading) --- */
  progressSection.hidden = false;
  resultsSection.hidden = true;
  emptyState.hidden = true;

  var badgeVariant = "processing";
  setBadge(progressBadge, statusLabels[job.status] || job.status, badgeVariant);

  counter.textContent = total > 0 ? job.downloaded + "/" + total : "0/?";
  progressBar.style.width = percent + "%";
  progressPercent.textContent = percent + "%";
  currentFile.textContent = job.current || "";
  updateChecklist(job);
  setWorkingState(true);
  btnDownload.disabled = true;
  btnText.textContent = job.status === "discovering" ? "Đang quét..." : "Đang tải...";

  return false;
}

/* ─── POLLING ─── */

/**
 * Poll the backend for job status updates every second.
 * Stops when the job finishes.
 * @param {string} jobId - The job ID to poll.
 */
async function pollJob(jobId) {
  clearTimeout(pollTimer);
  try {
    var job = await request("/jobs/" + jobId);
    var finished = render(job);
    if (!finished) {
      pollTimer = setTimeout(function () {
        pollJob(jobId);
      }, 1000);
    }
  } catch (err) {
    showMessage(err.message, "error");
    btnDownload.disabled = false;
    setWorkingState(false);
    btnText.textContent = "Qu\u00e9t v\u00e0 t\u1ea3i \u00e2m thanh";
  }
}

/* ─── EVENT: DOWNLOAD BUTTON ─── */

btnDownload.addEventListener("click", async function () {
  var url = urlInput.value.trim();

  /* Validate URL */
  if (!/^https?:\/\//i.test(url)) {
    showMessage("D\u00e1n li\u00ean k\u1ebft b\u1eaft \u0111\u1ea7u b\u1eb1ng http:// ho\u1eb7c https://", "error");
    return;
  }

  /* Disable button and reset UI */
  btnDownload.disabled = true;
  setWorkingState(true);
  btnText.textContent = "\u0110ang g\u1eedi...";
  btnOpen.hidden = true;
  resultsSection.hidden = true;
  emptyState.hidden = true;
  showMessage("");
  resetChecklist();

  try {
    var job = await request("/jobs", {
      method: "POST",
      body: JSON.stringify({ url: url })
    });
    activeJobId = job.id;

    /* Persist the job ID and URL for popup reopening */
    await chrome.storage.local.set({
      lastJobId: job.id,
      lastUrl: url
    });

    render(job);
    pollJob(job.id);
  } catch (err) {
    showMessage(err.message, "error");
    btnDownload.disabled = false;
    setWorkingState(false);
    btnText.textContent = "Qu\u00e9t v\u00e0 t\u1ea3i \u00e2m thanh";
  }
});

/* ─── EVENT: OPEN FOLDER BUTTON ─── */

btnOpen.addEventListener("click", async function () {
  if (!activeJobId) {
    return;
  }
  try {
    await request("/open-folder", {
      method: "POST",
      body: JSON.stringify({ job_id: activeJobId })
    });
  } catch (err) {
    showMessage(err.message, "error");
  }
});

/* ─── INITIALIZATION ─── */

(async function init() {
  /* Restore last session from chrome.storage */
  var saved = await chrome.storage.local.get(["lastJobId", "lastUrl"]);
  urlInput.value = saved.lastUrl || "";
  activeJobId = saved.lastJobId || null;

  /* Check server connection */
  await checkServer();

  /* Resume polling if there was an active job */
  if (activeJobId) {
    pollJob(activeJobId);
  }
})();
