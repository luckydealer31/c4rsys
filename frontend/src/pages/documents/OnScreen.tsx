/** Signing by the client on the screen of the employee's device (ТЗ § 10.2.2, § 18.4.2; decision
 * В-8.3): the client reads the version, marks the consent and signs by a finger, or refuses. */
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { ApiError, uploadFile } from "../../api/client";
import type { DocumentSigner, DocumentSigning } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCompany } from "../../layout/CompanyShell";

type Point = { x: number; y: number };
const HEIGHT = 220;

/** A pad to draw by a finger, a stylus or a mouse; the strokes are kept to undo the last one. */
function SignaturePad({ strokes, onChange }: { strokes: Point[][]; onChange: (next: Point[][]) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawing = useRef<Point[] | null>(null);

  function paint(all: Point[][]) {
    const canvas = ref.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    if (canvas.width !== Math.round(width * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(HEIGHT * ratio);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, HEIGHT);
    context.strokeStyle = "#111111";
    context.lineWidth = 2.5;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of all) {
      context.beginPath();
      stroke.forEach((p, index) => (index === 0 ? context.moveTo(p.x, p.y) : context.lineTo(p.x, p.y)));
      const [first] = stroke;
      if (stroke.length === 1 && first) context.lineTo(first.x + 0.1, first.y + 0.1);
      context.stroke();
    }
  }

  useEffect(() => paint(strokes));

  function at(event: PointerEvent<HTMLCanvasElement>): Point {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  return (
    <canvas
      ref={ref}
      className="signature-pad"
      style={{ height: HEIGHT }}
      aria-label="signature"
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        drawing.current = [at(event)];
        paint([...strokes, drawing.current]);
      }}
      onPointerMove={(event) => {
        if (!drawing.current) return;
        drawing.current.push(at(event));
        paint([...strokes, drawing.current]);
      }}
      onPointerUp={() => {
        if (!drawing.current) return;
        const stroke = drawing.current;
        drawing.current = null;
        onChange([...strokes, stroke]);
      }}
      onPointerCancel={() => {
        drawing.current = null;
        paint(strokes);
      }}
    />
  );
}

function canvasPng(strokes: Point[][]): Promise<File> {
  const canvas = document.querySelector<HTMLCanvasElement>("canvas.signature-pad");
  return new Promise((resolve, reject) => {
    if (!canvas || strokes.length === 0) {
      reject(new Error("empty"));
      return;
    }
    canvas.toBlob((blob) => (blob ? resolve(new File([blob], "signature.png", { type: "image/png" })) : reject(new Error("empty"))), "image/png");
  });
}

/** The full screen of signing: the version, the consent, the pad; or a refusal with a comment. */
export function OnScreenSigning({
  documentId,
  title,
  content,
  signer,
  onClose,
  onDone,
}: {
  documentId: string;
  title: string;
  content: string;
  signer: DocumentSigner;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const answer = useCommand<Record<string, unknown>, { signing: DocumentSigning }>(
    `/c/${company.public_id}/documents/${documentId}/signers/${signer.id}/on-screen`,
  );
  const [consent, setConsent] = useState(false);
  const [strokes, setStrokes] = useState<Point[][]>([]);
  const [refusing, setRefusing] = useState(false);
  const [comment, setComment] = useState("");
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = before;
    };
  }, []);

  async function sign() {
    setUploadError(null);
    setBusy(true);
    try {
      let fileId: string;
      try {
        fileId = (await uploadFile("document_signature", await canvasPng(strokes))).id;
      } catch (caught) {
        setUploadError(caught instanceof ApiError ? caught : new ApiError(0, null));
        return;
      }
      const result = await answer.run({ version_number: signer.version_number, decision: "sign", consent, file_id: fileId, comment: "" });
      if (result) await onDone();
    } finally {
      setBusy(false);
    }
  }

  async function refuse() {
    const result = await answer.run({ version_number: signer.version_number, decision: "decline", consent: false, comment });
    if (result) await onDone();
  }

  const pending = busy || answer.pending;
  // Over the whole screen, outside of the cards of the page (a phone, § 10.2.2).
  return createPortal(
    <div className="screen-sign" role="dialog" aria-modal="true" aria-label={t("documents.on_screen_title")}>
      <div className="screen-sign-head">
        <strong>{title}</strong>
        <button type="button" className="secondary" onClick={onClose} disabled={pending}>
          {t("documents.on_screen_close")}
        </button>
      </div>
      <p className="small muted">
        {t("documents.version", { number: signer.version_number })} · {t(`documents.signer_${signer.role}`)}: {signer.name}
      </p>
      <p className="small">{t("documents.on_screen_read")}</p>
      <div className="document-content screen-sign-content" dangerouslySetInnerHTML={{ __html: content }} />
      {!refusing ? (
        <>
          <label className="screen-sign-consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> {t("documents.on_screen_consent")}
          </label>
          <p className="small muted">{t("documents.on_screen_draw")}</p>
          <SignaturePad strokes={strokes} onChange={setStrokes} />
          <div className="actions">
            <button type="button" className="secondary" disabled={pending || strokes.length === 0} onClick={() => setStrokes(strokes.slice(0, -1))}>
              {t("documents.on_screen_undo")}
            </button>
            <button type="button" className="secondary" disabled={pending || strokes.length === 0} onClick={() => setStrokes([])}>
              {t("documents.on_screen_clear")}
            </button>
          </div>
          <div className="actions">
            <button type="button" disabled={pending || !consent || strokes.length === 0} onClick={() => void sign()}>
              {t("documents.on_screen_sign")}
            </button>
            <button type="button" className="danger" disabled={pending} onClick={() => setRefusing(true)}>
              {t("documents.on_screen_refuse")}
            </button>
          </div>
        </>
      ) : (
        <>
          <Field label={t("documents.on_screen_refuse_reason")}>
            <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="button" className="danger" disabled={pending} onClick={() => void refuse()}>
              {t("documents.on_screen_refuse_confirm")}
            </button>
            <button type="button" className="secondary" disabled={pending} onClick={() => setRefusing(false)}>
              {t("documents.on_screen_back")}
            </button>
          </div>
        </>
      )}
      <ErrorMessage error={uploadError ?? answer.error} />
    </div>,
    document.body,
  );
}
