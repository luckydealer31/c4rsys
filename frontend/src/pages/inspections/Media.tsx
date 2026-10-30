/** Photos and videos of inspections (§ 10.4.1–10.4.2): taken by the camera or chosen from the
 * device, uploaded at once; a video is checked for its duration first and compressed by the
 * server afterwards (decision В-8.1). */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, fileUrl, uploadWithProgress, type UploadedFile } from "../../api/client";
import { ErrorMessage } from "../../components/ui";
import {
  isVideoFile,
  localError,
  MAX_PHOTO_BYTES,
  MAX_VIDEO_SECONDS,
  PHOTO_TYPES,
  videoDuration,
} from "./data";

export function PhotoThumb({ fileId, size = 96, onRemove }: { fileId: string; size?: number; onRemove?: () => void }) {
  const { t } = useTranslation();
  return (
    <span className="media-thumb" style={{ width: size, height: size }}>
      <a href={fileUrl(fileId)} target="_blank" rel="noreferrer">
        <img src={fileUrl(fileId)} alt="" loading="lazy" />
      </a>
      {onRemove ? (
        <button type="button" className="media-remove" aria-label={t("inspections.remove_file")} onClick={onRemove}>
          ×
        </button>
      ) : null}
    </span>
  );
}

export function VideoItem({
  fileId,
  state,
  onRemove,
}: {
  fileId: string;
  state?: "ready" | "processing" | "failed";
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="media-video">
      {state === "processing" ? (
        <div className="media-video-wait">
          <span className="badge warn">{t("inspections.video_processing")}</span>
        </div>
      ) : (
        <video src={fileUrl(fileId)} controls preload="metadata" playsInline />
      )}
      {state === "failed" ? <p className="muted small">{t("inspections.video_failed")}</p> : null}
      {onRemove ? (
        <button type="button" className="secondary" onClick={onRemove}>
          {t("inspections.remove_file")}
        </button>
      ) : null}
    </div>
  );
}

type Targets = {
  onUploaded?: (file: UploadedFile) => void | Promise<void>;
  onLocal?: (file: File) => void | Promise<void>;
  local?: boolean;
};

/** Uploads the file, or keeps it in the local draft without connection (§ 10.4.2). */
async function send(
  file: File,
  purpose: "inspection_photo" | "inspection_video",
  { onUploaded, onLocal, local }: Targets,
  setProgress: (value: number | null) => void,
  setError: (error: ApiError | null) => void,
): Promise<void> {
  try {
    if (onLocal && (local || !navigator.onLine || !onUploaded)) {
      await onLocal(file);
      return;
    }
    if (!onUploaded) return;
    setProgress(0);
    let uploaded: UploadedFile;
    try {
      uploaded = await uploadWithProgress(purpose, file, setProgress);
    } catch (caught) {
      if (onLocal && caught instanceof ApiError && caught.status === 0) {
        await onLocal(file);
        return;
      }
      throw caught;
    }
    await onUploaded(uploaded);
  } catch (caught) {
    setError(caught instanceof ApiError ? caught : new ApiError(0, null));
  } finally {
    setProgress(null);
  }
}

/** Two ways for a photo: the camera of the phone or a file of the device (§ 10.4.2). */
export function PhotoInput({
  onUploaded,
  onLocal,
  local,
  disabled,
  compact,
}: {
  onUploaded?: (file: UploadedFile) => void | Promise<void>;
  /** Without connection the chosen file is kept in the local draft (§ 10.4.2). */
  onLocal?: (file: File) => void | Promise<void>;
  /** Always into the local draft (an inspection started offline, § 10.4.3). */
  local?: boolean;
  disabled?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const camera = useRef<HTMLInputElement>(null);
  const chooser = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function take(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    for (const file of Array.from(files)) {
      if (!PHOTO_TYPES.includes(file.type)) {
        setError(localError("FILE_TYPE_NOT_ALLOWED_PHOTO"));
        continue;
      }
      if (file.size > MAX_PHOTO_BYTES) {
        setError(localError("FILE_TOO_LARGE", { max_mb: 5 }));
        continue;
      }
      await send(file, "inspection_photo", { onUploaded, onLocal, local }, setProgress, setError);
    }
    if (camera.current) camera.current.value = "";
    if (chooser.current) chooser.current.value = "";
  }

  const busy = disabled || progress !== null;
  return (
    <div className="media-input">
      <input
        ref={camera}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => void take(e.target.files)}
      />
      <input
        ref={chooser}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        hidden
        onChange={(e) => void take(e.target.files)}
      />
      <button type="button" disabled={busy} onClick={() => camera.current?.click()}>
        {compact ? t("inspections.take_photo_short") : t("inspections.take_photo")}
      </button>
      <button type="button" className="secondary" disabled={busy} onClick={() => chooser.current?.click()}>
        {compact ? t("inspections.choose_photo_short") : t("inspections.choose_photo")}
      </button>
      {progress !== null ? (
        <span className="muted small">{t("inspections.uploading", { percent: Math.round(progress * 100) })}</span>
      ) : null}
      <ErrorMessage error={error} />
    </div>
  );
}

/** Videos from the files of the device, up to 2 minutes each (§ 10.4.2). A longer one is
 * refused, never trimmed. */
export function VideoInput({
  onUploaded,
  onLocal,
  local,
  disabled,
}: {
  onUploaded?: (file: UploadedFile) => void | Promise<void>;
  onLocal?: (file: File) => void | Promise<void>;
  local?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function take(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    for (const file of Array.from(files)) {
      if (!isVideoFile(file)) {
        setError(localError("FILE_TYPE_NOT_ALLOWED_VIDEO"));
        continue;
      }
      const seconds = await videoDuration(file);
      if (seconds !== null && seconds > MAX_VIDEO_SECONDS) {
        setError(localError("VIDEO_TOO_LONG", { max_seconds: MAX_VIDEO_SECONDS, seconds: Math.floor(seconds) }));
        continue;
      }
      await send(file, "inspection_video", { onUploaded, onLocal, local }, setProgress, setError);
    }
    if (input.current) input.current.value = "";
  }

  return (
    <div className="media-input">
      <input
        ref={input}
        type="file"
        accept="video/mp4,video/quicktime,.mp4,.mov"
        multiple
        hidden
        onChange={(e) => void take(e.target.files)}
      />
      <button type="button" className="secondary" disabled={disabled || progress !== null} onClick={() => input.current?.click()}>
        {t("inspections.add_video")}
      </button>
      {progress !== null ? (
        <span className="muted small">
          {progress < 1 ? t("inspections.uploading", { percent: Math.round(progress * 100) }) : t("inspections.checking_video")}
        </span>
      ) : null}
      <span className="muted small">{t("inspections.video_hint")}</span>
      <ErrorMessage error={error} />
    </div>
  );
}
