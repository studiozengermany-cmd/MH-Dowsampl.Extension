import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  FolderInput,
  FolderOpen,
  Inbox,
  Link2,
  RotateCcw,
  X as XIcon,
} from "lucide-react";

import {
  type Job,
  type MessageView,
  type PopupState,
  type SettingsView,
  type StepState,
  canDownload,
  computePercent,
  stepsFor,
} from "./model";

export type PopupProps = {
  state: PopupState;
  job: Job;
  settings: SettingsView;
  message: MessageView;
  url: string;
  onUrlChange: (value: string) => void;
  onClearUrl: () => void;
  onDownload: () => void;
  onCancel: () => void;
  onReset: () => void;
  onOpenFolder: () => void;
  onChooseFolder: () => void;
  onAskEachTimeChange: (value: boolean) => void;
};

export function PopupFrame({ children }: { children: ReactNode }) {
  return (
    <div
      className="mh-popup-shell overflow-hidden rounded-[24px]"
      style={{ width: 400, height: 600 }}
    >
      <div className="relative z-[1] flex h-full flex-col overflow-hidden">{children}</div>
    </div>
  );
}

export function Popup({
  state,
  job,
  settings,
  message,
  url,
  onUrlChange,
  onClearUrl,
  onDownload,
  onCancel,
  onReset,
  onOpenFolder,
  onChooseFolder,
  onAskEachTimeChange,
}: PopupProps) {
  const online = state !== "offline";
  const logoActive =
    state === "queued" ||
    state === "discovering" ||
    state === "downloading" ||
    state === "analyzing" ||
    state === "cancelling";
  const hasExtras =
    state === "queued" ||
    state === "discovering" ||
    state === "downloading" ||
    state === "analyzing" ||
    state === "cancelling" ||
    state === "cancelled" ||
    state === "completed" ||
    state === "empty" ||
    state === "failed";
  const gated = !canDownload(state, settings);

  return (
    <div className="flex h-full flex-col">
      <StatusBar
        online={online}
        downloadRoot={settings.savedPath ?? "Chưa chọn"}
        remote={settings.savedPath === "Chrome / Cốc Cốc"}
      />
      <div className="flex flex-col gap-3 px-5 pt-4 pb-3">
        <BrandHeader active={logoActive} />
        <SaveLocationRow
          settings={settings}
          showToggle={
            state === "idle" ||
            state === "offline" ||
            state === "no-location" ||
            state === "picking-folder" ||
            state === "pick-cancelled" ||
            state === "completed" ||
            state === "failed" ||
            state === "empty" ||
            state === "cancelled"
          }
          onChooseFolder={onChooseFolder}
          onAskEachTimeChange={onAskEachTimeChange}
        />
      </div>
      <div className="mh-scroll flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 pb-5 pt-1">
        {hasExtras ? (
          <>
            <PrimaryCard
              state={state}
              url={url}
              onUrlChange={onUrlChange}
              onClearUrl={onClearUrl}
              onDownload={onDownload}
              onCancel={onCancel}
              offlineText={message.text}
            />
            {(state === "queued" ||
              state === "discovering" ||
              state === "downloading" ||
              state === "analyzing" ||
              state === "cancelling") && (
              <ProcessingSection job={job} onCancel={onCancel} />
            )}
            {state === "completed" && (
              <CompletionCard job={job} settings={settings} onOpenFolder={onOpenFolder} onReset={onReset} />
            )}
            {state === "empty" && <EmptyCard />}
            {state === "failed" && <FailedCard job={job} onReset={onReset} />}
            {message.text && <MessageArea text={message.text} kind={message.kind} />}
          </>
        ) : (
          <div className="flex flex-1 flex-col justify-center gap-3">
            <PrimaryCard
              state={state}
              gated={gated}
              url={url}
              onUrlChange={onUrlChange}
              onClearUrl={onClearUrl}
              onDownload={onDownload}
              onCancel={onCancel}
              offlineText={message.text}
            />
            {message.text && <MessageArea text={message.text} kind={message.kind} />}
          </div>
        )}
      </div>
    </div>
  );
}

function BrandMark({ active = false, size = 42 }: { active?: boolean; size?: number }) {
  return (
    <div
      className={
        "mh-brand-mark relative inline-grid place-items-center " +
        (active ? "mh-brand-logo-active" : "")
      }
      style={{ width: size, height: size }}
      aria-label="MH-Dowsample.Extension"
    >
      <MHMonogram size={Math.round(size * 0.62)} />
    </div>
  );
}

function MHMonogram({ size = 26 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 44 32"
      width={size * (44 / 32)}
      height={size}
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      <path
        d="M3 27 V5 H8 L15 18 L22 5 H27 V27 H22 V13 L16.5 22 H13.5 L8 13 V27 Z"
        fill="#FFFFFF"
      />
      <rect x="30" y="5" width="4" height="22" fill="#FFFFFF" />
      <rect x="37" y="5" width="4" height="22" fill="#FFFFFF" />
      <rect x="30" y="13" width="11" height="5" fill="#FFFFFF" />
      <rect x="34.5" y="14.5" width="2" height="2" fill="#10B981" />
    </svg>
  );
}

function BrandHeader({ active = false }: { active?: boolean }) {
  return (
    <div className="mh-brand-cluster flex w-full items-center gap-3">
      <BrandMark active={active} size={44} />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
        <h1
          className="inline-flex max-w-full items-baseline whitespace-nowrap font-[family-name:var(--font-mono)] text-[18px] font-extrabold leading-none tracking-tight text-gray-900"
          aria-label="MH-Dowsample.Extension"
        >
          <span>MH</span>
          <span className="mx-0.5">-</span>
          <span>Dowsample</span>
          <span className="mx-0.5 text-emerald-500">.</span>
          <span>Extension</span>
        </h1>
        <p className="mt-1 text-[12px] font-normal leading-snug text-gray-400">
          Thu thập âm thanh · WAV gốc
        </p>
      </div>
    </div>
  );
}

function StatusBar({
  online,
  downloadRoot,
  remote,
}: {
  online: boolean;
  downloadRoot: string;
  remote: boolean;
}) {
  return (
    <div className="flex h-8 items-center justify-between border-b border-[color:var(--stroke-divider)] px-4">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={
            "block h-2 w-2 shrink-0 rounded-full " +
            (online
              ? "mh-pulse bg-[color:var(--color-emerald)]"
              : "bg-[color:var(--color-signal)]")
          }
        />
        <span
          className="min-w-0 truncate font-[family-name:var(--font-display)] text-[12px] font-medium text-[color:var(--color-ink)]"
          title={online ? downloadRoot : undefined}
        >
          {online ? "Server kết nối" : "Server ngoại tuyến"}
        </span>
      </div>
      <span className="ml-2 shrink-0 rounded-full bg-black/[0.04] px-2 py-0.5 font-mono text-[11px] text-[#4B5563]">
        {remote ? "Render API" : "Port 8765"}
      </span>
    </div>
  );
}

function PrimaryCard({
  state,
  gated = false,
  url,
  onUrlChange,
  onClearUrl,
  onDownload,
  onCancel,
  offlineText,
}: {
  state: PopupState;
  gated?: boolean;
  url: string;
  onUrlChange: (value: string) => void;
  onClearUrl: () => void;
  onDownload: () => void;
  onCancel: () => void;
  offlineText?: string;
}) {
  const online = state !== "offline";
  const badgeText =
    state === "offline"
      ? "KHÔNG KẾT NỐI"
      : state === "no-location"
        ? "CHƯA CHỌN NƠI LƯU"
        : state === "picking-folder"
          ? "ĐANG CHỌN THƯ MỤC"
          : state === "pick-cancelled"
            ? "ĐÃ HỦY CHỌN"
            : state === "queued"
              ? "XẾP HÀNG"
              : state === "discovering"
                ? "ĐANG TÌM KIẾM"
                : state === "downloading"
                  ? "ĐANG TẢI"
                  : state === "analyzing"
                    ? "ĐANG XỬ LÝ"
                    : state === "cancelling"
                      ? "ĐANG HỦY"
                      : state === "cancelled"
                        ? "ĐÃ HỦY"
                        : state === "completed"
                          ? "HOÀN TẤT"
                          : state === "failed" || state === "empty"
                            ? "LỖI"
                            : "TRẠNG THÁI SẴN SÀNG";

  const badgeTone: "error" | "progress" | "success" | "neutral" =
    state === "offline" ||
    state === "no-location" ||
    state === "pick-cancelled" ||
    state === "failed" ||
    state === "empty"
      ? "error"
      : state === "picking-folder" ||
          state === "queued" ||
          state === "discovering" ||
          state === "downloading" ||
          state === "analyzing" ||
          state === "cancelling"
        ? "progress"
        : state === "completed"
          ? "success"
          : "neutral";

  const badgeStyle =
    badgeTone === "error"
      ? { background: "#FFF1F2", color: "#E11D48", border: "1px solid #FFE4E6" }
      : badgeTone === "progress"
        ? { background: "#EEF2FF", color: "#4F46E5", border: "1px solid #E0E7FF" }
        : badgeTone === "success"
          ? { background: "#ECFDF5", color: "#059669", border: "1px solid #D1FAE5" }
          : {
              background: "rgba(0, 0, 0, 0.04)",
              color: "#374151",
              border: "1px solid rgba(0, 0, 0, 0.06)",
            };

  const description = online
    ? gated
      ? "Cần chọn nơi lưu trước khi tải. Nhấn Thay đổi ở panel Nơi lưu bên trên."
      : "Dán liên kết Splice hoặc link audio trực tiếp để tải file gốc về máy."
    : offlineText || "Server chưa kết nối.";

  return (
    <section className="mh-action-panel flex flex-col gap-3.5 p-5">
      <div className="flex flex-col gap-2">
        <span
          className="self-start rounded-full px-3 py-1 font-[family-name:var(--font-display)] text-[11px] font-semibold tracking-[0.06em]"
          style={badgeStyle}
        >
          {badgeText}
        </span>
        <p className="text-[13px] leading-relaxed text-[#4B5563]">{description}</p>
      </div>
      <UrlInput
        state={state}
        disabled={gated}
        value={url}
        onChange={onUrlChange}
        onClear={onClearUrl}
      />
      <PrimaryCTA
        state={state}
        forceDisabled={gated}
        onClick={onDownload}
        onCancel={onCancel}
      />
    </section>
  );
}

function UrlInput({
  state,
  disabled = false,
  value,
  onChange,
  onClear,
}: {
  state: PopupState;
  disabled?: boolean;
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
}) {
  const isInputState =
    state === "idle" ||
    state === "queued" ||
    state === "discovering" ||
    state === "no-location" ||
    state === "picking-folder" ||
    state === "pick-cancelled" ||
    state === "completed" ||
    state === "failed" ||
    state === "empty" ||
    state === "cancelled";
  if (!isInputState) return null;
  const isBusy = state === "queued" || state === "discovering";
  const isDisabled = disabled || isBusy;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-1.5 font-[family-name:var(--font-display)] text-[11px] font-semibold uppercase tracking-[0.06em] text-[#4B5563]">
          <Link2 size={12} strokeWidth={2} className="text-[color:var(--color-steel)]" />
          Liên kết chứa âm thanh
        </span>
        {value && !isDisabled ? (
          <button
            type="button"
            onClick={onClear}
            className="flex items-center gap-1 font-[family-name:var(--font-display)] text-[11px] font-semibold text-[#6B7280] hover:text-[#1F2937]"
            aria-label="Xóa liên kết"
          >
            <XIcon size={11} strokeWidth={2} />
            Xóa
          </button>
        ) : null}
      </div>
      <textarea
        aria-label="Liên kết chứa âm thanh"
        rows={3}
        placeholder="https://splice.com/sounds/..."
        disabled={isDisabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mh-scroll min-w-0 resize-none rounded-[10px] border border-[color:var(--stroke-inner)] bg-white px-3 py-2 font-mono text-[12px] leading-relaxed text-[color:var(--color-ink)] placeholder:text-[#9CA3AF] focus:border-[color:var(--color-ink)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
    </div>
  );
}

function PrimaryCTA({
  state,
  forceDisabled = false,
  onClick,
  onCancel,
}: {
  state: PopupState;
  forceDisabled?: boolean;
  onClick: () => void;
  onCancel: () => void;
}) {
  const disabled = state === "offline" || forceDisabled;
  const busy =
    state === "queued" ||
    state === "discovering" ||
    state === "downloading" ||
    state === "analyzing";
  const cancelling = state === "cancelling";
  const label =
    state === "queued"
      ? "Đang gửi…"
      : state === "discovering"
        ? "Đang quét…"
        : state === "downloading" || state === "analyzing"
          ? "Đang tải…"
          : state === "cancelling"
            ? "Đang hủy…"
            : forceDisabled
              ? "Chọn nơi lưu trước"
              : "Quét và tải âm thanh";

  if (disabled) {
    return (
      <button
        type="button"
        disabled
        className="flex w-full cursor-not-allowed items-center justify-between rounded-[12px] bg-[#D1D5DB] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-[#9CA3AF]"
      >
        <span>{label}</span>
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/40">
          <ArrowRight size={14} strokeWidth={2} />
        </span>
      </button>
    );
  }

  if (busy || cancelling) {
    return (
      <button
        type="button"
        disabled={cancelling}
        onClick={onCancel}
        className="mh-cta-ink group flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white disabled:cursor-wait disabled:opacity-70"
      >
        <span className="flex items-center gap-2">
          {cancelling ? (
            <span className="mh-spin-slow inline-block h-3.5 w-3.5 rounded-full border-2 border-white/40 border-t-white" />
          ) : (
            <XIcon size={14} strokeWidth={2.5} />
          )}
          {cancelling ? "Đang hủy…" : "Hủy tác vụ"}
        </span>
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/15">
          <XIcon size={14} strokeWidth={2} />
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="mh-cta-emerald group flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white"
    >
      <span className="flex items-center gap-2">
        {label}
      </span>
      <span className="mh-ease flex h-7 w-7 items-center justify-center rounded-full bg-white/20 transition-transform duration-300 group-hover:translate-x-0.5">
        <ArrowRight size={14} strokeWidth={2} />
      </span>
    </button>
  );
}

function StepIcon({ state }: { state: StepState }) {
  if (state === "done") {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-emerald)] text-white">
        <Check size={10} strokeWidth={3} />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="mh-spin-slow flex h-4 w-4 shrink-0 rounded-full border-2 border-indigo-600 border-t-transparent" />
    );
  }
  if (state === "error") {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-rose-500 text-white">
        <XIcon size={10} strokeWidth={3} />
      </span>
    );
  }
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center text-[color:var(--color-steel)]">
      <Circle size={14} strokeWidth={1.5} />
    </span>
  );
}

function Checklist({ job }: { job: Job }) {
  const steps = stepsFor(job);
  const labels = [
    "Nhận liên kết",
    "Tìm đường dẫn âm thanh",
    "Tải file gốc",
    "Hoàn tất xử lý",
  ];
  return (
    <ul className="flex flex-col gap-2 border-t border-[color:var(--stroke-divider)] pt-3">
      {labels.map((label, index) => {
        const step = steps[index];
        return (
          <li key={label} className="flex items-center gap-2.5">
            <StepIcon state={step} />
            <span
              className={
                "text-[12.5px] " +
                (step === "done"
                  ? "text-[color:var(--color-ink)]"
                  : step === "active"
                    ? "font-semibold text-indigo-600"
                    : step === "error"
                      ? "text-rose-600 font-semibold"
                      : "text-[#9CA3AF]")
              }
            >
              {label}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ProcessingSection({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const total = job.discovered;
  const percentage = computePercent(job);
  const badge =
    job.status === "queued"
      ? "XẾP HÀNG"
      : job.status === "discovering"
        ? "ĐANG TÌM KIẾM"
        : job.status === "analyzing"
          ? "ĐANG XỬ LÝ"
          : job.status === "cancelling"
            ? "ĐANG HỦY"
            : "ĐANG TẢI";
  const counterText = total > 0 ? `${job.downloaded}/${total}` : "0/?";

  return (
    <section className="mh-card flex flex-col gap-4 p-6">
      <div className="flex flex-col gap-3">
        <div className="flex items-end justify-between gap-4">
          <span className="rounded-md bg-indigo-50 px-2.5 py-1 font-[family-name:var(--font-display)] text-[11px] font-bold tracking-[0.06em] text-indigo-600 border border-indigo-100">
            {badge}
          </span>
          <span
            className="font-[family-name:var(--font-display)] text-[34px] font-bold leading-none tracking-normal text-indigo-600"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {percentage}%
          </span>
        </div>
        <p
          className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-[#1F2937]"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          Đã tải {counterText} file
        </p>
        <div
          className="h-2.5 w-full overflow-hidden rounded-full border border-black/[0.035] bg-black/[0.055]"
          style={{
            boxShadow:
              "inset 0 1px 3px rgba(0,0,0,0.08), 0 1px 0 rgba(255,255,255,0.8)",
          }}
        >
          <div
            className="mh-ease h-full w-full rounded-full origin-left will-change-transform transition-transform duration-500"
            style={{
              transform: `scaleX(${percentage / 100})`,
              background: "linear-gradient(90deg, #4338CA 0%, #6366F1 100%)",
              boxShadow: "inset 0 1px 0 rgba(255,255,255,0.2)",
            }}
            aria-valuenow={percentage}
            aria-valuemin={0}
            aria-valuemax={100}
            role="progressbar"
            aria-label="Tiến trình tải xuống"
          />
        </div>
        {job.current ? (
          <p className="truncate text-[12px] text-[#4B5563]" title={job.current}>
            Đang xử lý:{" "}
            <span className="font-mono text-[12px] font-medium text-[color:var(--color-ink)]">
              {job.current}
            </span>
          </p>
        ) : (
          <p className="text-[12px] italic text-[#9CA3AF]">
            {job.status === "queued"
              ? "Đang gửi yêu cầu…"
              : job.status === "discovering"
                ? "Đang đọc trang nguồn…"
                : "Đang chuẩn bị file…"}
          </p>
        )}
      </div>
      <Checklist job={job} />
      {job.output_dir ? <SaveLocation path={job.output_dir} /> : null}
      <button
        type="button"
        onClick={onCancel}
        className="mh-cta-ink group mt-1 flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white"
      >
        <span className="flex items-center gap-2">
          <XIcon size={14} strokeWidth={2.5} />
          Hủy tác vụ
        </span>
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/15">
          <XIcon size={14} strokeWidth={2} />
        </span>
      </button>
    </section>
  );
}

function MessageArea({ text, kind }: MessageView) {
  const color =
    kind === "success"
      ? "text-emerald-600"
      : kind === "error"
        ? "text-rose-600"
        : "text-[#6B7280]";
  return (
    <p role="status" aria-live="polite" className={"text-[12px] leading-relaxed " + color}>
      {text}
    </p>
  );
}

function CompletionCard({ job, settings, onOpenFolder, onReset }: { job: Job; settings: SettingsView; onOpenFolder: () => void; onReset: () => void }) {
  return (
    <section className="mh-complete relative shrink-0 overflow-hidden rounded-[20px] border p-6">
      <div className="relative flex flex-col gap-3">
        <div className="flex items-end justify-between gap-4">
          <span className="rounded-md bg-emerald-50 border border-emerald-100 px-2.5 py-1 font-[family-name:var(--font-display)] text-[11px] font-bold tracking-[0.06em] text-emerald-600">
            HOÀN TẤT
          </span>
          <span
            className="font-[family-name:var(--font-display)] text-[34px] font-bold leading-none tracking-normal text-emerald-600"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            100%
          </span>
        </div>
        <div className="flex items-center gap-3 pt-1">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white">
            <Check size={16} strokeWidth={2.5} />
          </span>
          <div className="flex flex-col">
            <p className="font-[family-name:var(--font-display)] text-[17px] font-semibold text-[color:var(--color-ink)]">
              Đã tải xuống thành công
            </p>
            <p
              className="text-[13px] text-[#4B5563]"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              Đã tải xong {job.downloaded} / {job.discovered} file
            </p>
          </div>
        </div>
        {job.failed > 0 && (
          <p
            className="text-[12px] text-rose-600 font-semibold"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            Lỗi tải: <strong>{job.failed}</strong>
          </p>
        )}
        <SaveLocation path={job.output_dir || settings.savedPath || "Chưa chọn"} label="THƯ MỤC LƯU" />
        <button
          type="button"
          onClick={onOpenFolder}
          className="mh-cta-ink group mt-1 flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white"
        >
          <span>Mở thư mục tải</span>
          <span className="mh-ease flex h-7 w-7 items-center justify-center rounded-full bg-white/15 transition-transform duration-300 group-hover:translate-x-0.5">
            <FolderOpen size={14} strokeWidth={2} />
          </span>
        </button>
        <button
          type="button"
          onClick={onReset}
          className="mh-cta-emerald group flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white"
        >
          <span className="flex items-center gap-2">
            <RotateCcw size={14} strokeWidth={2} />
            Tải link khác
          </span>
          <span className="mh-ease flex h-7 w-7 items-center justify-center rounded-full bg-white/20 transition-transform duration-300 group-hover:translate-x-0.5">
            <ArrowRight size={14} strokeWidth={2} />
          </span>
        </button>
      </div>
    </section>
  );
}

function EmptyCard() {
  return (
    <section className="mh-card flex flex-col items-center gap-3 px-5 py-8 text-center">
      <Inbox size={48} strokeWidth={1} className="text-[color:var(--color-steel)]" />
      <p className="text-[13px] text-[#4B5563]">
        Không tìm thấy link âm thanh trên trang này.
      </p>
    </section>
  );
}

function FailedCard({ job, onReset }: { job: Job; onReset: () => void }) {
  const percentage = computePercent(job);
  return (
    <section className="mh-card flex flex-col gap-4 p-6">
      <div className="flex flex-col gap-3">
        <div className="flex items-end justify-between gap-4">
          <span className="rounded-md bg-rose-50 border border-rose-100 px-2.5 py-1 font-[family-name:var(--font-display)] text-[11px] font-bold tracking-[0.06em] text-rose-600">
            THẤT BẠI
          </span>
          <span
            className="font-[family-name:var(--font-display)] text-[34px] font-bold leading-none tracking-normal text-rose-600"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {percentage}%
          </span>
        </div>
        <p className="text-[13px] text-[#4B5563]" style={{ fontVariantNumeric: "tabular-nums" }}>
          Đã tải {job.downloaded} / {job.discovered} file · Lỗi {job.failed}
        </p>
        <div className="flex items-start gap-2 rounded-[10px] border border-rose-100 bg-rose-50 px-3 py-2">
          <AlertTriangle
            size={14}
            strokeWidth={2}
            className="mt-0.5 shrink-0 text-rose-600"
          />
          <p className="text-[12px] leading-relaxed text-rose-700 font-medium">
            {job.error || "Không tải được file."}
          </p>
        </div>
      </div>
      <Checklist job={job} />
      <button
        type="button"
        onClick={onReset}
        className="mh-cta-emerald group flex w-full items-center justify-between rounded-[12px] px-5 py-[11px] font-[family-name:var(--font-display)] text-[14px] font-semibold text-white"
      >
        <span className="flex items-center gap-2">
          <RotateCcw size={14} strokeWidth={2} />
          Thử lại
        </span>
        <span className="mh-ease flex h-7 w-7 items-center justify-center rounded-full bg-white/20 transition-transform duration-300 group-hover:translate-x-0.5">
          <ArrowRight size={14} strokeWidth={2} />
        </span>
      </button>
    </section>
  );
}

function SaveLocation({ path, label = "THƯ MỤC LƯU" }: { path: string; label?: string }) {
  return (
    <div className="mt-3 flex flex-col gap-1 border-t border-[color:var(--stroke-divider)] pt-3">
      <div className="font-[family-name:var(--font-display)] text-[11px] font-semibold uppercase tracking-[0.06em] text-[#4B5563]">
        {label}
      </div>
      <div
        className="truncate font-mono text-[12px] leading-[1.5] text-[#1F2937]"
        title={path}
      >
        {path}
      </div>
    </div>
  );
}

function SaveLocationRow({
  settings,
  showToggle,
  onChooseFolder,
  onAskEachTimeChange,
}: {
  settings: SettingsView;
  showToggle: boolean;
  onChooseFolder: () => void;
  onAskEachTimeChange: (value: boolean) => void;
}) {
  const { savedPath, picking, cancelled } = settings;
  const missing = savedPath === null;

  return (
    <section
      className="flex flex-col gap-2 border-t border-[color:var(--stroke-divider)] pt-3"
      aria-labelledby="save-location-label"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FolderOpen
            size={12}
            strokeWidth={2}
            className="shrink-0 text-[color:var(--color-steel)]"
            aria-hidden="true"
          />
          <span id="save-location-label" className="sr-only">
            Nơi lưu
          </span>
          {missing ? (
            <div className="flex min-w-0 items-center gap-1.5">
              <span
                className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-rose-500"
                aria-hidden="true"
              />
              <span className="truncate font-[family-name:var(--font-display)] text-[12px] font-semibold text-rose-600">
                {picking
                  ? "Đang chờ chọn thư mục…"
                  : cancelled
                    ? "Đã hủy · chưa có thư mục"
                    : "Chưa chọn thư mục"}
              </span>
            </div>
          ) : (
            <div
              className="min-w-0 truncate font-mono text-[12px] leading-[1.4] text-[color:var(--color-ink)]"
              title={savedPath}
            >
              {savedPath}
            </div>
          )}
        </div>
        <button
          type="button"
          disabled={picking}
          onClick={onChooseFolder}
          aria-label="Thay đổi thư mục lưu"
          className={
            "mh-ease shrink-0 rounded-[8px] px-2.5 py-1 font-[family-name:var(--font-display)] text-[11px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 " +
            (missing
              ? "bg-[color:var(--color-ink)] text-white hover:bg-black"
              : "text-[color:var(--color-steel)] hover:text-[color:var(--color-ink)]")
          }
        >
          <span className="flex items-center gap-1">
            <FolderInput size={11} strokeWidth={2} />
            {picking ? "Đang mở…" : missing ? "Chọn thư mục" : "Thay đổi"}
          </span>
        </button>
      </div>

      {showToggle && (
        <AskEachTimeToggle
          disabled={missing}
          checked={settings.askEachTime}
          onChange={onAskEachTimeChange}
        />
      )}
    </section>
  );
}

function AskEachTimeToggle({
  disabled,
  checked,
  onChange,
}: {
  disabled: boolean;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label
      className={
        "flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-white/60 px-3.5 py-3 " +
        (disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer")
      }
    >
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[13px] font-bold leading-tight text-gray-900">
          Hỏi vị trí lưu từng tệp
        </span>
        <span className="text-[11.5px] font-normal leading-snug text-gray-500">
          Bật để Chrome/Cốc Cốc hỏi nơi lưu cho từng file trước khi tải.
        </span>
      </span>

      <span className="relative inline-flex shrink-0 items-center pt-0.5">
        <input
          type="checkbox"
          role="switch"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          disabled={disabled}
          aria-label="Hỏi vị trí lưu từng tệp"
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className={
            "relative inline-flex h-[22px] w-[40px] items-center rounded-full transition-colors duration-200 " +
            (checked ? "bg-green-500" : "bg-gray-200")
          }
        >
          <span
            className={
              "inline-block h-[18px] w-[18px] transform rounded-full bg-white shadow ring-1 ring-black/5 transition-transform duration-200 " +
              (checked ? "translate-x-[20px]" : "translate-x-[2px]")
            }
          />
        </span>
      </span>
    </label>
  );
}
