/** A file of a document opened in a window of the interface (customer's decision of 13.11.2026):
 * a picture or the pages of a PDF, without going to an address of the file. The file is read
 * with the session of the user, so the server decides every time whether it may be seen. */
import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "./ui";

const PdfPages = lazy(() => import("./PdfPages"));

type Loaded = { kind: "image"; url: string } | { kind: "pdf"; data: ArrayBuffer } | { kind: "other" } | { kind: "failed" };

function Content({ path, name }: { path: string; name: string }) {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    let cancelled = false;
    let url = "";
    void (async () => {
      try {
        const response = await fetch(`/api${path}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        if (cancelled) return;
        if (blob.type.startsWith("image/")) {
          url = URL.createObjectURL(blob);
          setLoaded({ kind: "image", url });
        } else if (blob.type === "application/pdf") {
          const data = await blob.arrayBuffer();
          if (!cancelled) setLoaded({ kind: "pdf", data });
        } else {
          setLoaded({ kind: "other" });
        }
      } catch {
        if (!cancelled) setLoaded({ kind: "failed" });
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [path]);

  const loading = <p className="muted">{t("common.loading")}</p>;
  if (loaded === null) return loading;
  if (loaded.kind === "image") return <img className="file-preview-image" src={loaded.url} alt={name} />;
  if (loaded.kind === "pdf") {
    return (
      <Suspense fallback={loading}>
        <PdfPages data={loaded.data} />
      </Suspense>
    );
  }
  return <p className="error-text">{t(loaded.kind === "other" ? "files.preview_unsupported" : "files.preview_failed")}</p>;
}

/** `path` — the address of the file in the API without «/api»; `name` — the title of the window. */
export function FilePreview({ path, name, label }: { path: string; name: string; label?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="link" aria-label={`${t("files.preview_open")}: ${name}`} onClick={() => setOpen(true)}>
        {label ?? t("vehicles.document_file")}
      </button>
      {open ? (
        <Modal wide title={name} onClose={() => setOpen(false)}>
          <Content path={path} name={name} />
        </Modal>
      ) : null}
    </>
  );
}
