import {
  StrictMode,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";

import { Popup, PopupFrame } from "./PopupView";
import {
  type Job,
  type MessageView,
  type PopupState,
  type SettingsView,
  EMPTY_JOB,
  defaultMessage,
  previewModel,
} from "./model";
import "./styles.css";

const LOCAL_API = "http://127.0.0.1:8765";
const BUILD_API = String(import.meta.env.VITE_MH_API_URL ?? "").trim();
const BUILD_ACCESS_KEY = String(import.meta.env.VITE_MH_ACCESS_KEY ?? "").trim();
let API = normalizeApiBase(BUILD_API || LOCAL_API);
let ACCESS_KEY = BUILD_ACCESS_KEY;
const REQUEST_TIMEOUT_MS = 15_000;
const POLL_INTERVAL_MS = 1_000;
const SERVER_RETRY_MS = 3_000;
const SERVER_WAKE_TIMEOUT_MS = 120_000;
const URL_MAX_LENGTH = 2_048;

type StorageValues = {
  lastJobId?: string;
  lastUrl?: string;
  apiBase?: string;
  accessKey?: string;
  askEachTime?: boolean;
};

type SettingsPayload = {
  download_root?: unknown;
  download_root_configured?: unknown;
  ask_each_time?: unknown;
};

type ChromeStorage = {
  get: (keys: string[]) => Promise<StorageValues>;
  set: (items: StorageValues) => Promise<void>;
  remove: (keys: string[]) => Promise<void>;
};

type ChromeDownloads = {
  download: (options: {
    url: string;
    filename?: string;
    conflictAction?: "uniquify" | "overwrite" | "prompt";
    saveAs?: boolean;
    headers?: Array<{ name: string; value: string }>;
  }) => Promise<number>;
  showDefaultFolder?: () => void;
};

declare const chrome:
  | {
      storage?: {
        local?: ChromeStorage;
      };
      downloads?: ChromeDownloads;
    }
  | undefined;

class RequestError extends Error {
  status?: number;
}

type JobFile = {
  id: string;
  name: string;
  relative_path: string;
  size: number;
  download_url: string;
};

type JobFilesPayload = {
  job_id: string;
  files: JobFile[];
  total: number;
};

type SourceAsset = {
  url: string;
  title?: string;
  bpm?: number;
  musical_key?: string;
  declared_format?: string;
};

const AUDIO_EXTENSIONS = new Set([
  "wav",
  "mp3",
  "flac",
  "ogg",
  "m4a",
  "aac",
  "aiff",
  "aif",
  "opus",
]);

function normalizeApiBase(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) return LOCAL_API;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return LOCAL_API;
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return LOCAL_API;
  }
}

function applyRuntimeConfig(values: StorageValues): void {
  API = normalizeApiBase(values.apiBase || BUILD_API || LOCAL_API);
  ACCESS_KEY = values.accessKey || BUILD_ACCESS_KEY;
}

function remoteApi(): boolean {
  return API !== LOCAL_API;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(API + path, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(ACCESS_KEY ? { "X-MH-Access-Key": ACCESS_KEY } : {}),
        ...(options?.headers ?? {}),
      },
    });
    const payload = (await response.json().catch(() => ({}))) as T & { error?: string };
    if (!response.ok) {
      const error = new RequestError(payload.error || `Lỗi HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new RequestError("Server không phản hồi");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function normalizeSettings(payload: SettingsPayload): SettingsView {
  const configured = payload.download_root_configured === true;
  return {
    savedPath:
      configured && typeof payload.download_root === "string" ? payload.download_root : null,
    askEachTime: payload.ask_each_time === true,
    picking: false,
    cancelled: false,
  };
}

function stateForJob(job: Job): PopupState {
  if (
    job.status === "failed" &&
    job.discovered === 0 &&
    job.error.includes("Không tìm thấy")
  ) {
    return "empty";
  }
  return job.status;
}

function isTerminalJob(job: Job): boolean {
  return job.status === "completed" || job.status === "failed" || job.status === "cancelled";
}

function validateUrl(value: string): string | null {
  if (!value) return "Hãy dán liên kết trước khi tải.";
  if (value.length > URL_MAX_LENGTH) {
    return `Liên kết quá dài (>${URL_MAX_LENGTH} ký tự).`;
  }
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "Dán liên kết bắt đầu bằng http:// hoặc https://";
    }
  } catch {
    return "Liên kết không hợp lệ.";
  }
  return null;
}

function sourceFormat(item: Record<string, unknown>): string | undefined {
  const fields = [
    item.asset_file_type_slug,
    item.file_type,
    item.fileType,
    item.format,
    item.extension,
    item.mime_type,
    item.mimeType,
  ];
  const aliases: Record<string, string> = {
    wave: "wav",
    aif: "aiff",
    aifc: "aiff",
    mpeg: "mp3",
    mp4: "m4a",
    aac: "m4a",
    opus: "ogg",
  };
  for (const raw of fields) {
    const value = String(raw ?? "").toLowerCase();
    if (value.includes("preview")) continue;
    const token = value.split("/").pop()?.replace(/^\./, "") ?? "";
    const normalized = aliases[token] ?? token;
    if (AUDIO_EXTENSIONS.has(normalized)) return normalized;
  }
  try {
    const extension = new URL(String(item.url ?? "")).pathname.split(".").pop()?.toLowerCase();
    return extension && AUDIO_EXTENSIONS.has(extension) ? aliases[extension] ?? extension : undefined;
  } catch {
    return undefined;
  }
}

function previewAsset(item: Record<string, unknown>): boolean {
  const labels = [
    item.asset_file_type_slug,
    item.file_type,
    item.fileType,
    item.kind,
    item.role,
    item.name,
    item.url,
  ]
    .map((value) => String(value ?? ""))
    .join(" ")
    .toLowerCase();
  return item.preview === true || item.is_preview === true || item.isPreview === true || labels.includes("preview");
}

function catalogueText(item: Record<string, unknown>, fields: string[]): string | undefined {
  const candidates: Record<string, unknown>[] = [item];
  for (const value of Object.values(item)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      candidates.push(value as Record<string, unknown>);
    }
  }
  for (const candidate of candidates) {
    for (const field of fields) {
      const raw = candidate[field];
      if (typeof raw === "string" && raw.trim()) return raw.trim();
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        const nested = raw as Record<string, unknown>;
        for (const key of ["name", "title", "value", "display_name", "displayName"]) {
          if (typeof nested[key] === "string" && String(nested[key]).trim()) {
            return String(nested[key]).trim();
          }
        }
      }
    }
  }
  return undefined;
}

function catalogueBpm(item: Record<string, unknown>): number | undefined {
  const candidates: Record<string, unknown>[] = [item];
  for (const value of Object.values(item)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      candidates.push(value as Record<string, unknown>);
    }
  }
  for (const candidate of candidates) {
    for (const field of ["bpm", "tempo"]) {
      const match = String(candidate[field] ?? "").match(/\d+(?:\.\d+)?/);
      const value = match ? Math.round(Number(match[0])) : 0;
      if (value >= 20 && value <= 400) return value;
    }
  }
  return undefined;
}

function catalogueKey(item: Record<string, unknown>): string | undefined {
  return catalogueText(item, [
    "musical_key",
    "musicalKey",
    "key_name",
    "keyName",
    "tonality",
    "key",
  ]);
}

function extractOriginalAssets(documentText: string): SourceAsset[] {
  const document = new DOMParser().parseFromString(documentText, "text/html");
  const assets: SourceAsset[] = [];
  const seen = new Set<string>();
  let foundPreview = false;

  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    const item = value as Record<string, unknown>;
    if (Array.isArray(item.files)) {
      const candidates = item.files.filter(
        (entry): entry is Record<string, unknown> =>
          Boolean(entry) && typeof entry === "object" && typeof (entry as Record<string, unknown>).url === "string",
      );
      foundPreview ||= candidates.some(previewAsset);
      const originals = candidates.filter((entry) => !previewAsset(entry) && sourceFormat(entry));
      const order: Record<string, number> = {
        wav: 0,
        aiff: 1,
        flac: 2,
        ogg: 3,
        m4a: 4,
        mp3: 5,
      };
      originals.sort(
        (left, right) => (order[sourceFormat(left) ?? ""] ?? 50) - (order[sourceFormat(right) ?? ""] ?? 50),
      );
      const preferred = originals[0];
      const originalUrl = preferred ? String(preferred.url) : "";
      if (originalUrl && !seen.has(originalUrl)) {
        assets.push({
          url: originalUrl,
          title: catalogueText(item, ["name", "title", "display_name", "displayName"]),
          bpm: catalogueBpm(item),
          musical_key: catalogueKey(item),
          declared_format: sourceFormat(preferred),
        });
        seen.add(originalUrl);
      }
    }
    Object.values(item).forEach(visit);
  };

  for (const script of document.querySelectorAll("script[data-sveltekit-fetched]")) {
    try {
      const envelope = JSON.parse(script.textContent?.trim() || "null") as unknown;
      const body =
        envelope && typeof envelope === "object"
          ? (envelope as Record<string, unknown>).body
          : undefined;
      visit(typeof body === "string" ? JSON.parse(body) : body);
    } catch {
      // Ignore unrelated/incomplete embedded payloads and continue scanning.
    }
  }
  if (assets.length === 0 && foundPreview) {
    throw new RequestError("Không truy cập được file WAV gốc bằng phiên đăng nhập hiện tại.");
  }
  return assets;
}

async function sourceAssetsFor(value: string): Promise<SourceAsset[] | undefined> {
  const parsed = new URL(value);
  const directExtension = parsed.pathname.split(".").pop()?.toLowerCase() ?? "";
  if (AUDIO_EXTENSIONS.has(directExtension)) {
    if (parsed.pathname.toLowerCase().includes("preview")) {
      throw new RequestError("Liên kết chỉ trỏ tới file nghe thử, không phải file gốc.");
    }
    return [
      {
        url: value,
        title: decodeURIComponent(parsed.pathname.split("/").pop() ?? "sample").replace(/\.[^.]+$/, ""),
        declared_format: directExtension,
      },
    ];
  }
  const hostname = parsed.hostname.toLowerCase();
  if (hostname !== "splice.com" && !hostname.endsWith(".splice.com")) return undefined;

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(value, {
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new RequestError(
        "Không truy cập được file WAV gốc bằng phiên đăng nhập hiện tại.",
      );
    }
    const assets = extractOriginalAssets(await response.text());
    if (assets.length === 0) {
      throw new RequestError("Không tìm thấy đường dẫn file âm thanh gốc trên trang.");
    }
    return assets;
  } catch (error) {
    if (error instanceof RequestError) throw error;
    throw new RequestError("Không truy cập được file WAV gốc bằng phiên đăng nhập hiện tại.");
  } finally {
    window.clearTimeout(timeout);
  }
}

function storageLocal(): ChromeStorage | null {
  if (typeof chrome === "undefined") return null;
  return chrome.storage?.local ?? null;
}

async function safeStorageGet(): Promise<StorageValues> {
  try {
    return (
      (await storageLocal()?.get([
        "lastJobId",
        "lastUrl",
        "apiBase",
        "accessKey",
        "askEachTime",
      ])) ?? {}
    );
  } catch {
    return {};
  }
}

function browserDownloads(): ChromeDownloads | null {
  if (typeof chrome === "undefined") return null;
  return chrome.downloads ?? null;
}

function safeDownloadRelativePath(value: string): string {
  const parts = value
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part && part !== "." && part !== "..");
  return parts.join("/") || "sample";
}

async function deliverJobFiles(jobId: string, saveAs: boolean): Promise<number> {
  const downloads = browserDownloads();
  if (!downloads) {
    throw new RequestError("Trình duyệt chưa cấp quyền tải file cho Extension.");
  }
  const payload = await request<JobFilesPayload>(`/jobs/${jobId}/files`);
  if (!Array.isArray(payload.files) || payload.files.length === 0) {
    throw new RequestError("Server không trả về file âm thanh hoàn thành.");
  }
  let started = 0;
  for (const file of payload.files) {
    const relative = safeDownloadRelativePath(file.relative_path || file.name);
    await downloads.download({
      url: API + file.download_url,
      filename: `MH-Dowsample/${jobId.slice(0, 8)}/${relative}`,
      conflictAction: "uniquify",
      saveAs,
      headers: ACCESS_KEY ? [{ name: "X-MH-Access-Key", value: ACCESS_KEY }] : undefined,
    });
    started += 1;
  }
  return started;
}

async function safeStorageSet(values: StorageValues): Promise<void> {
  try {
    await storageLocal()?.set(values);
  } catch {
    // The popup remains usable when extension storage is temporarily unavailable.
  }
}

async function safeStorageRemove(keys: string[]): Promise<void> {
  try {
    await storageLocal()?.remove(keys);
  } catch {
    // The stale identifier is also cleared in memory.
  }
}

function AuroraFrame({ children }: { children: ReactNode }) {
  return (
    <div className="mh-aurora h-[600px] w-[400px] font-sans text-[color:var(--color-ink)]">
      <div aria-hidden="true" className="mh-aurora__blobs">
        <span className="mh-blob mh-blob--blue" />
        <span className="mh-blob mh-blob--violet" />
        <span className="mh-blob mh-blob--peach" />
        <span className="mh-blob mh-blob--mint" />
      </div>
      <div aria-hidden="true" className="mh-aurora__veil" />
      {children}
    </div>
  );
}

function RuntimePopup() {
  const [state, setState] = useState<PopupState>("offline");
  const [job, setJob] = useState<Job>({ ...EMPTY_JOB });
  const [settings, setSettings] = useState<SettingsView>({
    savedPath: null,
    askEachTime: false,
    picking: false,
    cancelled: false,
  });
  const [url, setUrl] = useState("");
  const [messageOverride, setMessageOverride] = useState<MessageView | null>(null);
  const activeJobId = useRef<string | null>(null);
  const currentJobId = useRef<string | null>(null);
  const starting = useRef(false);
  const pollTimer = useRef<number | null>(null);
  const retryTimer = useRef<number | null>(null);
  const pollFailures = useRef(0);
  const wakeStartedAt = useRef<number | null>(null);
  const browserSaveAs = useRef(false);
  const deliveredJobs = useRef(new Set<string>());
  const mounted = useRef(true);

  const clearPollTimer = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const clearRetryTimer = useCallback(() => {
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
  }, []);

  const applyJob = useCallback((nextJob: Job) => {
    if (!mounted.current) return;
    setJob(nextJob);
    setState(stateForJob(nextJob));
    setMessageOverride(null);
  }, []);

  const finishJobLifecycle = useCallback((jobId: string) => {
    currentJobId.current = jobId;
    activeJobId.current = null;
    setUrl("");
    void safeStorageRemove(["lastJobId", "lastUrl"]);
  }, []);

  const loadServerState = useCallback(async (): Promise<boolean> => {
    clearRetryTimer();
    if (remoteApi()) {
      wakeStartedAt.current ??= Date.now();
      setState("offline");
      setMessageOverride({ text: "Đang khởi động server…", kind: "" });
    }
    try {
      const health = await request<SettingsPayload & { ok?: boolean }>("/health");
      if (!mounted.current) return false;
      const nextSettings = normalizeSettings(health);
      if (remoteApi()) nextSettings.askEachTime = browserSaveAs.current;
      wakeStartedAt.current = null;
      setSettings(nextSettings);
      if (!activeJobId.current) {
        setState(nextSettings.savedPath ? "idle" : "no-location");
        setMessageOverride(null);
      }
      return true;
    } catch (error) {
      if (!mounted.current) return false;
      setState("offline");
      if (remoteApi() && error instanceof RequestError && error.status === 403) {
        setMessageOverride({
          text: "Mã truy cập Extension không đúng hoặc chưa được cấu hình trên Render.",
          kind: "error",
        });
        return false;
      }
      const elapsed = Date.now() - (wakeStartedAt.current ?? Date.now());
      if (remoteApi() && elapsed >= SERVER_WAKE_TIMEOUT_MS) {
        setMessageOverride({
          text: "Server chưa phản hồi sau 2 phút. Hãy kiểm tra địa chỉ Render hoặc thử lại sau.",
          kind: "error",
        });
        return false;
      }
      setMessageOverride(
        remoteApi()
          ? { text: "Đang khởi động server…", kind: "" }
          : {
              text: error instanceof Error ? error.message : "Local server chưa phản hồi.",
              kind: "error",
            },
      );
      retryTimer.current = window.setTimeout(() => {
        void loadServerState();
      }, SERVER_RETRY_MS);
      return false;
    }
  }, [clearRetryTimer]);

  const pollJob = useCallback(
    async (jobId: string): Promise<void> => {
      clearPollTimer();
      if (activeJobId.current !== jobId) return;
      try {
        const nextJob = await request<Job>(`/jobs/${jobId}`);
        pollFailures.current = 0;
        if (
          nextJob.status === "completed" &&
          remoteApi() &&
          !deliveredJobs.current.has(jobId)
        ) {
          setState("analyzing");
          setJob({ ...nextJob, status: "analyzing", current: "Đang chuyển file về trình duyệt…" });
          setMessageOverride({ text: "Đang chuyển file về Chrome/Cốc Cốc…", kind: "" });
          try {
            await deliverJobFiles(jobId, browserSaveAs.current);
            deliveredJobs.current.add(jobId);
          } catch (error) {
            const message =
              error instanceof Error ? error.message : "Không thể tải file về trình duyệt.";
            applyJob({ ...nextJob, status: "failed", error: message });
            finishJobLifecycle(jobId);
            return;
          }
        }
        applyJob(nextJob);
        if (isTerminalJob(nextJob)) {
          finishJobLifecycle(jobId);
        } else {
          pollTimer.current = window.setTimeout(() => {
            void pollJob(jobId);
          }, POLL_INTERVAL_MS);
        }
      } catch (error) {
        if (!mounted.current || activeJobId.current !== jobId) return;
        if (error instanceof RequestError && error.status === 404) {
          activeJobId.current = null;
          await safeStorageRemove(["lastJobId"]);
          await loadServerState();
          setMessageOverride({
            text: "Tác vụ cũ đã kết thúc khi server khởi động lại. Bạn có thể bắt đầu tác vụ mới.",
            kind: "error",
          });
          return;
        }
        pollFailures.current += 1;
        setMessageOverride({
          text: "Mất kết nối tạm thời, đang thử lại…",
          kind: "error",
        });
        pollTimer.current = window.setTimeout(
          () => void pollJob(jobId),
          Math.min(5_000, POLL_INTERVAL_MS * pollFailures.current),
        );
      }
    },
    [applyJob, clearPollTimer, finishJobLifecycle, loadServerState],
  );

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      const saved = await safeStorageGet();
      if (!mounted.current) return;
      applyRuntimeConfig(saved);
      browserSaveAs.current = saved.askEachTime === true;
      setUrl(saved.lastUrl ?? "");
      activeJobId.current = saved.lastJobId ?? null;
      currentJobId.current = saved.lastJobId ?? null;
      const online = await loadServerState();
      if (online && activeJobId.current) {
        void pollJob(activeJobId.current);
      }
    })();

    return () => {
      mounted.current = false;
      clearPollTimer();
      clearRetryTimer();
    };
  }, [clearPollTimer, clearRetryTimer, loadServerState, pollJob]);

  const handleDownload = useCallback(async () => {
    if (starting.current || activeJobId.current) {
      setMessageOverride({
        text: "Đang có một tác vụ chạy. Hãy hủy hoặc chờ tác vụ đó hoàn tất.",
        kind: "error",
      });
      return;
    }
    const value = url.trim();
    const validationError = validateUrl(value);
    if (validationError) {
      setMessageOverride({ text: validationError, kind: "error" });
      return;
    }

    clearPollTimer();
    starting.current = true;
    pollFailures.current = 0;
    currentJobId.current = null;
    void safeStorageRemove(["lastJobId", "lastUrl"]);
    const queuedJob: Job = {
      ...EMPTY_JOB,
      status: "queued",
    };
    setJob(queuedJob);
    setState("queued");
    setMessageOverride(null);

    try {
      const sourceAssets = await sourceAssetsFor(value);
      const nextJob = await request<Job>("/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: value,
          ...(sourceAssets ? { assets: sourceAssets } : {}),
        }),
      });
      if (!nextJob.id) throw new RequestError("Server trả về dữ liệu không hợp lệ.");
      activeJobId.current = nextJob.id;
      currentJobId.current = nextJob.id;
      await safeStorageSet({ lastJobId: nextJob.id, lastUrl: value });
      applyJob(nextJob);
      if (isTerminalJob(nextJob)) {
        finishJobLifecycle(nextJob.id);
      } else {
        void pollJob(nextJob.id);
      }
    } catch (error) {
      activeJobId.current = null;
      currentJobId.current = null;
      void safeStorageRemove(["lastJobId"]);
      const text = error instanceof Error ? error.message : "Không thể bắt đầu tác vụ.";
      if (text.toLocaleLowerCase("vi").includes("hủy chọn")) {
        setState(settings.savedPath ? "idle" : "pick-cancelled");
      } else {
        setState(settings.savedPath ? "idle" : "no-location");
      }
      setJob({ ...EMPTY_JOB });
      setMessageOverride({ text, kind: "error" });
    } finally {
      starting.current = false;
    }
  }, [applyJob, clearPollTimer, finishJobLifecycle, pollJob, settings.savedPath, url]);

  const handleClearUrl = useCallback(() => {
    setUrl("");
    setMessageOverride(null);
    void safeStorageRemove(["lastUrl"]);
  }, []);

  const handleCancel = useCallback(async () => {
    const jobId = activeJobId.current;
    if (!jobId) {
      setMessageOverride({ text: "Không có tác vụ đang chạy để hủy.", kind: "error" });
      return;
    }

    clearPollTimer();
    setState("cancelling");
    setJob((current) => ({ ...current, status: "cancelling", current: "" }));
    setMessageOverride(null);
    try {
      const nextJob = await request<Job>(`/jobs/${jobId}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      applyJob(nextJob);
      if (isTerminalJob(nextJob)) {
        finishJobLifecycle(jobId);
      } else {
        void pollJob(jobId);
      }
    } catch (error) {
      setMessageOverride({
        text: error instanceof Error ? error.message : "Không thể hủy tác vụ.",
        kind: "error",
      });
      void pollJob(jobId);
    }
  }, [applyJob, clearPollTimer, finishJobLifecycle, pollJob]);

  const handleChooseFolder = useCallback(async () => {
    if (remoteApi()) {
      setMessageOverride({
        text: "Khi dùng Render, vị trí lưu do Chrome/Cốc Cốc quản lý trong phần Tải xuống.",
        kind: "",
      });
      return;
    }
    const previous = settings;
    setState("picking-folder");
    setSettings({
      savedPath: null,
      askEachTime: previous.askEachTime,
      picking: true,
      cancelled: false,
    });
    setMessageOverride(null);
    try {
      const payload = await request<SettingsPayload>("/settings/download-root", {
        method: "POST",
        body: JSON.stringify({ select: true }),
      });
      const nextSettings = normalizeSettings(payload);
      setSettings(nextSettings);
      setState(nextSettings.savedPath ? "idle" : "no-location");
      setMessageOverride(null);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Không thể chọn thư mục.";
      if (previous.savedPath) {
        setSettings(previous);
        setState("idle");
        setMessageOverride({ text, kind: "error" });
      } else {
        setSettings({ ...previous, savedPath: null, picking: false, cancelled: true });
        setState("pick-cancelled");
        setMessageOverride(null);
      }
    }
  }, [settings]);

  const handleAskEachTimeChange = useCallback(
    async (value: boolean) => {
      const previous = settings;
      setSettings({ ...settings, askEachTime: value });
      setMessageOverride(null);
      if (remoteApi()) {
        browserSaveAs.current = value;
        await safeStorageSet({ askEachTime: value });
        return;
      }
      try {
        const payload = await request<SettingsPayload>("/settings/download-root", {
          method: "POST",
          body: JSON.stringify({ ask_each_time: value }),
        });
        setSettings(normalizeSettings(payload));
      } catch (error) {
        setSettings(previous);
        setMessageOverride({
          text: error instanceof Error ? error.message : "Không thể lưu tùy chọn.",
          kind: "error",
        });
      }
    },
    [settings],
  );

  const handleOpenFolder = useCallback(async () => {
    if (remoteApi()) {
      const downloads = browserDownloads();
      if (downloads?.showDefaultFolder) {
        downloads.showDefaultFolder();
        return;
      }
      setMessageOverride({
        text: "Hãy mở mục Tải xuống của Chrome/Cốc Cốc để xem file.",
        kind: "",
      });
      return;
    }
    if (!currentJobId.current) {
      setMessageOverride({ text: "Chưa có thư mục tải để mở.", kind: "error" });
      return;
    }
    try {
      await request<{ ok: boolean }>("/open-folder", {
        method: "POST",
        body: JSON.stringify({ job_id: currentJobId.current }),
      });
    } catch (error) {
      setMessageOverride({
        text: error instanceof Error ? error.message : "Không thể mở thư mục tải.",
        kind: "error",
      });
    }
  }, []);

  const handleReset = useCallback(() => {
    clearPollTimer();
    activeJobId.current = null;
    currentJobId.current = null;
    setUrl("");
    setJob({ ...EMPTY_JOB });
    setState(settings.savedPath ? "idle" : "no-location");
    setMessageOverride(null);
    void safeStorageRemove(["lastJobId", "lastUrl"]);
  }, [clearPollTimer, settings.savedPath]);

  const message = messageOverride ?? defaultMessage(state, job);

  return (
    <Popup
      state={state}
      job={job}
      settings={settings}
      message={message}
      url={url}
      onUrlChange={setUrl}
      onClearUrl={handleClearUrl}
      onDownload={() => void handleDownload()}
      onCancel={() => void handleCancel()}
      onReset={handleReset}
      onOpenFolder={() => void handleOpenFolder()}
      onChooseFolder={() => void handleChooseFolder()}
      onAskEachTimeChange={(value) => void handleAskEachTimeChange(value)}
    />
  );
}

function PreviewPopup({ state }: { state: PopupState }) {
  const model = useMemo(() => previewModel(state), [state]);
  return (
    <Popup
      state={state}
      job={model.job}
      settings={model.settings}
      message={model.message}
      url=""
      onUrlChange={() => undefined}
      onClearUrl={() => undefined}
      onDownload={() => undefined}
      onCancel={() => undefined}
      onReset={() => undefined}
      onOpenFolder={() => undefined}
      onChooseFolder={() => undefined}
      onAskEachTimeChange={() => undefined}
    />
  );
}

function App() {
  const preview = new URLSearchParams(window.location.search).get("preview") as PopupState | null;
  return (
    <AuroraFrame>
      <PopupFrame>{preview ? <PreviewPopup state={preview} /> : <RuntimePopup />}</PopupFrame>
    </AuroraFrame>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing popup root element.");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
