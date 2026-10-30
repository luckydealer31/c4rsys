/**
 * HTTP client of the c4rsys API (docs/stage1/STAGE1_COMMANDS_API.md).
 *
 * - Session cookie and CSRF token (A08): the CSRF cookie is requested once and sent back in
 *   X-CSRFToken on every change.
 * - Single error format: every refusal becomes an ApiError with a stable code and a translation
 *   key; the interface never shows raw server text.
 * - A lost session is announced so the interface can return to the login screen.
 */

export type ErrorBody = {
  error: {
    code: string;
    message_key: string;
    params: Record<string, unknown>;
    field: string | null;
    request_id: string;
  };
};

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly params: Record<string, unknown>;
  readonly field: string | null;

  constructor(status: number, body: ErrorBody["error"] | null) {
    super(body?.code ?? `HTTP_${status}`);
    this.status = status;
    this.code = body?.code ?? (status === 0 ? "NETWORK_ERROR" : "INTERNAL_ERROR");
    this.params = body?.params ?? {};
    this.field = body?.field ?? null;
  }
}

export const SESSION_LOST_EVENT = "c4r:session-lost";

function cookie(name: string): string | null {
  const match = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

let csrfRequest: Promise<void> | null = null;

async function csrfToken(): Promise<string> {
  if (!cookie("csrftoken")) {
    csrfRequest ??= fetch("/api/auth/csrf", { credentials: "same-origin" }).then(() => undefined);
    await csrfRequest;
    csrfRequest = null;
  }
  return cookie("csrftoken") ?? "";
}

/** A file made by the server from a POST body, saved by the browser (§ 21.2.9). */
export async function downloadFile(path: string, body: unknown, filename: string): Promise<void> {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": await csrfToken() },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let payload: ErrorBody | null = null;
    try {
      payload = (await response.json()) as ErrorBody;
    } catch {
      payload = null;
    }
    throw new ApiError(response.status, payload?.error ?? null);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  /** Requests the interface makes by itself do not extend the session (§ 24.4.2). */
  background?: boolean;
};

/** Upload of one file as multipart (the browser sets the content type and its boundary). */
export async function uploadFile(
  purpose:
    | "avatar"
    | "company_logo"
    | "item_photo"
    | "warehouse_document"
    | "company_document"
    | "task_attachment"
    | "inspection_photo"
    | "inspection_video",
  file: File,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<{ id: string }> {
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await fetch(`/api/files?purpose=${purpose}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": await csrfToken(),
        "Idempotency-Key": idempotencyKey,
      },
      credentials: "same-origin",
      body,
    });
  } catch {
    throw new ApiError(0, null);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, (data as ErrorBody | null)?.error ?? null);
  }
  return (data as { file: { id: string } }).file;
}

export type UploadedFile = {
  id: string;
  content_type: string;
  /** A video is compressed on the server after the upload (decision В-8.1). */
  state?: "ready" | "processing" | "failed";
  duration_seconds?: string | null;
};

/** Upload of an inspection photo or video with its progress (0..1): a phone may send a large
 * original over a slow connection (§ 10.4.2). */
export async function uploadWithProgress(
  purpose: "inspection_photo" | "inspection_video",
  file: File,
  onProgress: (share: number) => void,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<UploadedFile> {
  const token = await csrfToken();
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/files?purpose=${purpose}`);
    request.withCredentials = true;
    request.setRequestHeader("Accept", "application/json");
    request.setRequestHeader("X-CSRFToken", token);
    request.setRequestHeader("Idempotency-Key", idempotencyKey);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onerror = () => reject(new ApiError(0, null));
    request.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(request.responseText);
      } catch {
        data = null;
      }
      if (request.status < 200 || request.status >= 300) {
        reject(new ApiError(request.status, (data as ErrorBody | null)?.error ?? null));
        return;
      }
      resolve((data as { file: UploadedFile }).file);
    };
    const body = new FormData();
    body.append("file", file);
    request.send(body);
  });
}

/** Upload of an import file (§ 22.2.2): the server checks it and stores the operation. */
export async function uploadImport<T>(
  path: string,
  file: File,
  fields: Record<string, string> = {},
): Promise<T> {
  const body = new FormData();
  body.append("file", file);
  for (const [name, value] of Object.entries(fields)) body.append(name, value);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": await csrfToken(),
        "Idempotency-Key": crypto.randomUUID(),
      },
      credentials: "same-origin",
      body,
    });
  } catch {
    throw new ApiError(0, null);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, (data as ErrorBody | null)?.error ?? null);
  }
  return data as T;
}

export function fileUrl(id: string): string {
  return `/api/files/${id}`;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = await csrfToken();
  }
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options.background) headers["X-C4R-Background"] = "1";

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, null);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new ApiError(response.status, (data as ErrorBody | null)?.error ?? null);
    if (error.code === "SESSION_REQUIRED" || error.code === "SESSION_EXPIRED") {
      window.dispatchEvent(new Event(SESSION_LOST_EVENT));
    }
    throw error;
  }
  return data as T;
}
