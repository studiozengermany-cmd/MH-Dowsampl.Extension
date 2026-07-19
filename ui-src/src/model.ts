export type PopupState =
  | "idle"
  | "offline"
  | "no-location"
  | "picking-folder"
  | "pick-cancelled"
  | "queued"
  | "discovering"
  | "downloading"
  | "analyzing"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "empty"
  | "failed";

export type Job = {
  id?: string;
  status:
    | "queued"
    | "discovering"
    | "downloading"
    | "analyzing"
    | "cancelling"
    | "cancelled"
    | "completed"
    | "failed";
  discovered: number;
  downloaded: number;
  failed: number;
  current: string;
  output_dir: string;
  error: string;
};

export type SettingsView = {
  savedPath: string | null;
  askEachTime: boolean;
  picking: boolean;
  cancelled: boolean;
};

export type MessageView = {
  text: string;
  kind: "success" | "error" | "";
};

export type StepState = "pending" | "active" | "done" | "error";

export const EMPTY_JOB: Job = {
  status: "discovering",
  discovered: 0,
  downloaded: 0,
  failed: 0,
  current: "",
  output_dir: "",
  error: "",
};

export function canDownload(state: PopupState, settings: SettingsView): boolean {
  if (state === "offline") return false;
  return settings.savedPath !== null && !settings.picking;
}

export function computePercent(job: Job): number {
  if (job.discovered <= 0) return 0;
  const done = Math.min(job.discovered, job.downloaded + job.failed);
  return Math.min(100, Math.max(0, Math.round((done * 100) / job.discovered)));
}

export function defaultMessage(state: PopupState, job: Job): MessageView {
  if (state === "offline") {
    return {
      text: "Hãy mở file START-SERVER.cmd trong thư mục dự án.",
      kind: "error",
    };
  }
  if (state === "no-location") {
    return {
      text: "Chưa chọn nơi lưu. Hãy nhấn Thay đổi để chọn thư mục trước khi tải.",
      kind: "error",
    };
  }
  if (state === "picking-folder") {
    return { text: "Đang mở hộp chọn thư mục của Windows…", kind: "" };
  }
  if (state === "pick-cancelled") {
    return {
      text: "Bạn đã hủy chọn thư mục. Chưa có nơi lưu mặc định.",
      kind: "error",
    };
  }
  if (state === "completed") {
    const base = `Đã tải ${job.downloaded} file gốc`;
    return {
      text: job.failed > 0 ? `${base} · ${job.failed} lỗi` : base,
      kind: "success",
    };
  }
  if (state === "cancelled") {
    return { text: "Đã hủy tác vụ. Bạn có thể dán liên kết mới.", kind: "" };
  }
  if (state === "failed" || state === "empty") {
    return { text: job.error || "Không tải được file.", kind: "error" };
  }
  return { text: "", kind: "" };
}

export function stepsFor(job: Job): StepState[] {
  switch (job.status) {
    case "queued":
      return ["done", "pending", "pending", "pending"];
    case "discovering":
      return ["done", "active", "pending", "pending"];
    case "downloading":
      return ["done", "done", "active", "pending"];
    case "analyzing":
      return ["done", "done", "done", "active"];
    case "cancelling":
      return ["done", "done", "active", "pending"];
    case "cancelled":
      return ["done", "done", "error", "pending"];
    case "completed":
      return ["done", "done", "done", "done"];
    case "failed":
      return job.discovered === 0
        ? ["done", "error", "pending", "pending"]
        : ["done", "done", "error", "pending"];
  }
}

const PREVIEW_OUTPUT_DIR =
  "J:\\MH-Audio-Downloads\\splice-vocal-chops-20260717-093214-a1b2c3";
const SAVE_PATH = "J:\\MH-Audio-Downloads";

export function previewModel(state: PopupState): {
  job: Job;
  settings: SettingsView;
  message: MessageView;
} {
  const jobs: Partial<Record<PopupState, Job>> = {
    queued: { ...EMPTY_JOB, status: "queued" },
    discovering: { ...EMPTY_JOB, status: "discovering" },
    downloading: {
      status: "downloading",
      discovered: 50,
      downloaded: 32,
      failed: 2,
      current: "splice-vocal-chop-Amin-120-034.wav",
      output_dir: PREVIEW_OUTPUT_DIR,
      error: "",
    },
    completed: {
      status: "completed",
      discovered: 50,
      downloaded: 48,
      failed: 2,
      current: "",
      output_dir: PREVIEW_OUTPUT_DIR,
      error: "",
    },
    empty: {
      status: "failed",
      discovered: 0,
      downloaded: 0,
      failed: 0,
      current: "",
      output_dir: "",
      error: "Không tìm thấy đường dẫn âm thanh trên trang này.",
    },
    failed: {
      status: "failed",
      discovered: 12,
      downloaded: 3,
      failed: 9,
      current: "",
      output_dir: PREVIEW_OUTPUT_DIR,
      error: "Không tải được file (HTTP 403 từ nguồn).",
    },
  };
  const settingsByState: Partial<Record<PopupState, SettingsView>> = {
    "no-location": {
      savedPath: null,
      askEachTime: false,
      picking: false,
      cancelled: false,
    },
    "picking-folder": {
      savedPath: null,
      askEachTime: false,
      picking: true,
      cancelled: false,
    },
    "pick-cancelled": {
      savedPath: null,
      askEachTime: false,
      picking: false,
      cancelled: true,
    },
  };
  const job = jobs[state] ?? { ...EMPTY_JOB };
  const settings = settingsByState[state] ?? {
    savedPath: SAVE_PATH,
    askEachTime: false,
    picking: false,
    cancelled: false,
  };
  return { job, settings, message: defaultMessage(state, job) };
}
