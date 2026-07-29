/* MH-Dowsample Extension — old popup with real job cancellation. */

const API = "http://127.0.0.1:8765";
const REQUEST_TIMEOUT_MS = 7000;
const POLL_INTERVAL_MS = 1000;
const POLL_RETRY_MAX_MS = 10000;

const serverStatus = document.querySelector("#server-status");
const serverText = document.querySelector("#server-text");
const statusBadge = document.querySelector("#status-badge");
const urlInput = document.querySelector("#url-input");
const btnDownload = document.querySelector("#btn-download");
const btnText = document.querySelector("#btn-text");
const btnCancel = document.querySelector("#btn-cancel");
const btnCancelText = document.querySelector("#btn-cancel-text");
const btnOpen = document.querySelector("#btn-open");
const progressSection = document.querySelector("#progress-section");
const progressBadge = document.querySelector("#progress-badge");
const progressLabel = document.querySelector("#progress-label");
const counter = document.querySelector("#counter");
const progressUnit = document.querySelector("#progress-unit");
const progressBar = document.querySelector("#progress-bar");
const progressPercent = document.querySelector("#progress-percent");
const currentFile = document.querySelector("#current-file");
const checkLink = document.querySelector("#check-link");
const checkDiscover = document.querySelector("#check-discover");
const checkDownload = document.querySelector("#check-download");
const checkDone = document.querySelector("#check-done");
const resultsSection = document.querySelector("#results-section");
const resultBadge = document.querySelector("#result-badge");
const resultPercent = document.querySelector("#result-percent");
const resultTitle = document.querySelector("#result-title");
const statDiscovered = document.querySelector("#stat-discovered");
const statDownloaded = document.querySelector("#stat-downloaded");
const statFailed = document.querySelector("#stat-failed");
const resultDir = document.querySelector("#result-dir");
const emptyState = document.querySelector("#empty-state");
const message = document.querySelector("#message");
const brandLogo = document.querySelector("#brand-logo");

let pollTimer = null;
let activeJobId = null;
let serverOnline = false;
let pollFailureCount = 0;

class ApiError extends Error {
  constructor(messageText, code, status) {
    super(messageText);
    this.name = "ApiError";
    this.code = code || "API_ERROR";
    this.status = status || 0;
  }
}

function showMessage(text, kind) {
  message.textContent = text || "";
  message.className = "message" + (kind ? " message--" + kind : "");
}

function setWorkingState(working) {
  brandLogo.classList.toggle("active", Boolean(working));
  btnDownload.classList.toggle("loading", Boolean(working));
}

function setBadge(badge, text, variant) {
  badge.textContent = text;
  badge.className = "badge" + (variant ? " badge--" + variant : "");
}

function setCheckState(icon, state) {
  const states = {
    done: ["check-icon check-icon--done", "✓"],
    active: ["check-icon check-icon--active", "◉"],
    error: ["check-icon check-icon--error", "✗"],
    pending: ["check-icon check-icon--pending", "○"]
  };
  const selected = states[state] || states.pending;
  icon.className = selected[0];
  icon.textContent = selected[1];
}

function resetChecklist() {
  setCheckState(checkLink, "pending");
  setCheckState(checkDiscover, "pending");
  setCheckState(checkDownload, "pending");
  setCheckState(checkDone, "pending");
}

function describeConnectionError(error) {
  if (error && error.code === "TIMEOUT") {
    return "Server phản hồi quá chậm. Hãy kiểm tra cửa sổ START-SERVER.cmd.";
  }
  if (error && error.code === "NETWORK") {
    return "Không kết nối được server local tại cổng 8765. Hãy mở START-SERVER.cmd.";
  }
  if (error && error.status === 403) {
    return "Server từ chối extension. Hãy cập nhật server và extension cùng một bản.";
  }
  return (error && error.message) || "Không kết nối được server local.";
}

async function request(path, options) {
  const controller = new AbortController();
  const timeout = setTimeout(function () {
    controller.abort();
  }, REQUEST_TIMEOUT_MS);

  try {
    let response;
    try {
      response = await fetch(API + path, {
        ...(options || {}),
        cache: "no-store",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...((options && options.headers) || {})
        }
      });
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new ApiError("Kết nối server quá thời gian", "TIMEOUT");
      }
      throw new ApiError("Không thể kết nối server local", "NETWORK");
    }

    const raw = await response.text();
    let payload = {};
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (_error) {
        throw new ApiError("Server trả về dữ liệu không hợp lệ", "INVALID_RESPONSE", response.status);
      }
    }
    if (!response.ok) {
      throw new ApiError(payload.error || "Lỗi HTTP " + response.status, "HTTP", response.status);
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

function parseUrls(value) {
  const lines = String(value || "")
    .split(/\r?\n/)
    .map(function (item) {
      return item.trim();
    })
    .filter(Boolean);

  const unique = [];
  const seen = new Set();
  for (const line of lines) {
    if (!/^https?:\/\//i.test(line)) {
      throw new Error("Mỗi liên kết phải bắt đầu bằng http:// hoặc https://");
    }
    if (!seen.has(line)) {
      unique.push(line);
      seen.add(line);
    }
  }
  if (!unique.length) {
    throw new Error("Hãy dán ít nhất một liên kết");
  }
  return unique;
}

async function checkServer(options) {
  const silent = Boolean(options && options.silent);
  try {
    const health = await request("/health");
    serverOnline = true;
    serverStatus.classList.add("online");
    const folder = health.download_root || "Chưa chọn thư mục lưu";
    serverText.textContent = "Server " + (health.version || "") + " · " + folder;
    if (!activeJobId) {
      btnDownload.disabled = false;
      setWorkingState(false);
      setBadge(statusBadge, "TRẠNG THÁI SẴN SÀNG", "ready");
    }
    if (!silent && !activeJobId) {
      showMessage(health.download_root_configured ? "" : "Chưa chọn thư mục; server sẽ hỏi một lần khi bắt đầu tải.");
    }
    return health;
  } catch (error) {
    serverOnline = false;
    serverStatus.classList.remove("online");
    serverText.textContent = "Server ngoại tuyến";
    btnDownload.disabled = true;
    setBadge(statusBadge, activeJobId ? "ĐANG KẾT NỐI LẠI" : "KHÔNG KẾT NỐI", activeJobId ? "processing" : "error");
    if (!silent) {
      showMessage(describeConnectionError(error), "error");
    }
    throw error;
  }
}

function updateChecklist(job) {
  if (job.cancel_requested) {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, job.discovered ? "done" : "active");
    setCheckState(checkDownload, "active");
    setCheckState(checkDone, "pending");
  } else if (job.status === "queued") {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, "pending");
    setCheckState(checkDownload, "pending");
    setCheckState(checkDone, "pending");
  } else if (job.status === "discovering") {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, "active");
    setCheckState(checkDownload, "pending");
    setCheckState(checkDone, "pending");
  } else if (job.status === "downloading") {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, "done");
    setCheckState(checkDownload, "active");
    setCheckState(checkDone, "pending");
  } else if (job.status === "completed") {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, "done");
    setCheckState(checkDownload, "done");
    setCheckState(checkDone, "done");
  } else if (job.status === "failed") {
    setCheckState(checkLink, "done");
    setCheckState(checkDiscover, job.discovered ? "done" : "error");
    setCheckState(checkDownload, job.discovered ? "error" : "pending");
    setCheckState(checkDone, "pending");
  }
}

function render(job) {
  const total = Number(job.discovered) || 0;
  const downloaded = Number(job.downloaded) || 0;
  const failed = Number(job.failed) || 0;
  const cancelledByUser = Boolean(job.cancelled_by_user);
  const done = downloaded + failed + (Number(job.cancelled) || 0);
  let percent = total > 0 ? Math.min(100, Math.round((done * 100) / total)) : 0;
  if (job.status === "discovering") {
    const sourceTotal = Number(job.source_total) || 0;
    const sourceProcessed = Number(job.source_processed) || 0;
    percent = sourceTotal > 0 ? Math.min(100, Math.round((sourceProcessed * 100) / sourceTotal)) : 0;
  }

  updateChecklist(job);

  if (job.status === "completed") {
    progressSection.hidden = true;
    emptyState.hidden = true;
    resultsSection.hidden = false;
    resultsSection.classList.toggle("is-cancelled", cancelledByUser);
    statDiscovered.textContent = String(total);
    statDownloaded.textContent = String(downloaded);
    statFailed.textContent = String(failed + (Number(job.source_failed) || 0));
    resultDir.textContent = job.output_dir || "";
    resultDir.title = job.output_dir || "";
    resultDir.hidden = !job.output_dir;
    btnOpen.hidden = !job.output_dir;
    btnCancel.hidden = true;
    btnDownload.disabled = false;
    setWorkingState(false);
    btnText.textContent = "Quét và tải âm thanh";

    if (cancelledByUser) {
      setBadge(statusBadge, "ĐÃ HỦY", "processing");
      setBadge(resultBadge, "ĐÃ HỦY", "processing");
      resultPercent.textContent = "—";
      resultTitle.textContent = "Đã dừng tác vụ";
      showMessage("Đã hủy tải. Các file hoàn tất trước lúc hủy vẫn được giữ lại.");
    } else {
      setBadge(statusBadge, "HOÀN TẤT", "ready");
      setBadge(resultBadge, "HOÀN TẤT", "ready");
      resultPercent.textContent = "100%";
      resultTitle.textContent = "Đã tải xuống thành công";
      const errors = failed + (Number(job.source_failed) || 0);
      showMessage("Đã tải " + downloaded + " file" + (errors ? " · " + errors + " lỗi" : ""), errors ? "" : "success");
    }
    return true;
  }

  if (job.status === "failed") {
    progressSection.hidden = true;
    resultsSection.hidden = true;
    emptyState.hidden = total === 0 ? false : true;
    btnCancel.hidden = true;
    setBadge(statusBadge, "LỖI", "error");
    const detail = job.error || (job.failures && job.failures[0]) || "Không tải được file.";
    showMessage(detail, "error");
    btnDownload.disabled = !serverOnline;
    setWorkingState(false);
    btnText.textContent = "Quét và tải âm thanh";
    return true;
  }

  progressSection.hidden = false;
  resultsSection.hidden = true;
  emptyState.hidden = true;
  btnCancel.hidden = false;
  btnCancel.disabled = Boolean(job.cancel_requested);
  btnCancelText.textContent = job.cancel_requested ? "Đang hủy..." : "Hủy tải";

  if (job.cancel_requested) {
    setBadge(progressBadge, "ĐANG HỦY", "processing");
  } else if (job.status === "discovering") {
    setBadge(progressBadge, "ĐANG TÌM KIẾM", "processing");
  } else if (job.status === "downloading") {
    setBadge(progressBadge, "ĐANG TẢI", "processing");
  } else {
    setBadge(progressBadge, "XẾP HÀNG", "processing");
  }

  if (job.status === "discovering") {
    progressLabel.textContent = "Đã quét";
    counter.textContent = String(Number(job.source_processed) || 0) + "/" + String(Number(job.source_total) || 0);
    progressUnit.textContent = "link";
  } else {
    progressLabel.textContent = "Đã tải";
    counter.textContent = total > 0 ? downloaded + "/" + total : "0/?";
    progressUnit.textContent = "file";
  }
  progressBar.style.width = percent + "%";
  progressPercent.textContent = percent + "%";
  currentFile.textContent = job.current || "";
  setWorkingState(true);
  btnDownload.disabled = true;
  btnText.textContent = job.cancel_requested ? "Đang hủy..." : (job.status === "discovering" ? "Đang quét..." : "Đang tải...");
  return false;
}

function schedulePoll(jobId, delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(function () {
    pollJob(jobId);
  }, delay);
}

function isRetriablePollError(error) {
  return Boolean(
    error &&
    (error.code === "NETWORK" ||
      error.code === "TIMEOUT" ||
      error.code === "INVALID_RESPONSE" ||
      Number(error.status) >= 500)
  );
}

async function pollJob(jobId) {
  clearTimeout(pollTimer);
  try {
    const job = await request("/jobs/" + jobId);
    pollFailureCount = 0;
    serverOnline = true;
    serverStatus.classList.add("online");
    const finished = render(job);
    if (!finished) {
      schedulePoll(jobId, POLL_INTERVAL_MS);
    }
  } catch (error) {
    if (error && error.status === 404) {
      activeJobId = null;
      pollFailureCount = 0;
      await chrome.storage.local.remove("lastJobId");
      btnDownload.disabled = !serverOnline;
      setWorkingState(false);
      showMessage("Tác vụ cũ không còn trong server. Anh có thể bắt đầu lượt mới.");
      return;
    }

    if (isRetriablePollError(error) && activeJobId === jobId) {
      pollFailureCount += 1;
      serverOnline = false;
      serverStatus.classList.remove("online");
      serverText.textContent = "Đang tự kết nối lại...";
      setBadge(statusBadge, "ĐANG KẾT NỐI LẠI", "processing");
      showMessage("Mất kết nối tạm thời. Tác vụ trên server vẫn tiếp tục.");
      const delay = Math.min(POLL_RETRY_MAX_MS, POLL_INTERVAL_MS * Math.pow(2, Math.min(pollFailureCount, 4)));
      schedulePoll(jobId, delay);
      return;
    }

    serverOnline = false;
    serverStatus.classList.remove("online");
    serverText.textContent = "Server ngoại tuyến";
    btnDownload.disabled = true;
    btnCancel.disabled = true;
    setWorkingState(false);
    setBadge(statusBadge, "MẤT KẾT NỐI", "error");
    showMessage(describeConnectionError(error), "error");
  }
}

btnDownload.addEventListener("click", async function () {
  let urls;
  try {
    urls = parseUrls(urlInput.value);
  } catch (error) {
    showMessage(error.message, "error");
    return;
  }

  clearTimeout(pollTimer);
  pollFailureCount = 0;
  btnDownload.disabled = true;
  setWorkingState(true);
  btnText.textContent = "Đang gửi...";
  btnOpen.hidden = true;
  resultsSection.hidden = true;
  emptyState.hidden = true;
  showMessage("");
  resetChecklist();

  try {
    const job = await request("/jobs", {
      method: "POST",
      body: JSON.stringify({ url: urls[0], urls: urls })
    });
    activeJobId = job.id;
    await chrome.storage.local.set({
      lastJobId: job.id,
      lastUrl: urlInput.value
    });
    render(job);
    pollJob(job.id);
  } catch (error) {
    btnDownload.disabled = !serverOnline;
    setWorkingState(false);
    btnText.textContent = "Quét và tải âm thanh";
    showMessage(error.code === "NETWORK" || error.code === "TIMEOUT" ? describeConnectionError(error) : error.message, "error");
  }
});

btnCancel.addEventListener("click", async function () {
  if (!activeJobId || btnCancel.disabled) {
    return;
  }
  btnCancel.disabled = true;
  btnCancelText.textContent = "Đang hủy...";
  showMessage("Đang yêu cầu server dừng tác vụ...");
  try {
    const job = await request("/jobs/" + activeJobId + "/cancel", {
      method: "POST",
      body: "{}"
    });
    render(job);
    schedulePoll(activeJobId, 250);
  } catch (error) {
    btnCancel.disabled = false;
    btnCancelText.textContent = "Hủy tải";
    showMessage(error.message || "Không thể hủy tác vụ.", "error");
  }
});

btnOpen.addEventListener("click", async function () {
  if (!activeJobId) {
    return;
  }
  try {
    await request("/open-folder", {
      method: "POST",
      body: JSON.stringify({ job_id: activeJobId })
    });
  } catch (error) {
    showMessage(error.message, "error");
  }
});

(async function init() {
  const saved = await chrome.storage.local.get(["lastJobId", "lastUrl"]);
  urlInput.value = saved.lastUrl || "";
  activeJobId = saved.lastJobId || null;
  try {
    await checkServer();
  } catch (_error) {
    if (activeJobId) {
      pollJob(activeJobId);
    }
    return;
  }
  if (activeJobId) {
    pollJob(activeJobId);
  }
})();
