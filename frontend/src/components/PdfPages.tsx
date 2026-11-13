/** The pages of a PDF drawn on the screen in place, on a phone as well: nothing is opened in
 * another tab and there is no address of the file to pass on. Loaded only when a PDF is opened. */
import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";
import workerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const MAX_PAGES = 50;
const SCALE = 2;

export default function PdfPages({ data }: { data: ArrayBuffer }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [cut, setCut] = useState(false);

  useEffect(() => {
    const holder = ref.current;
    let cancelled = false;
    // The viewer takes the bytes over, so it gets its own copy.
    const task = pdfjs.getDocument({ data: new Uint8Array(data.slice(0)), isEvalSupported: false });
    void (async () => {
      try {
        const file = await task.promise;
        const count = Math.min(file.numPages, MAX_PAGES);
        for (let number = 1; number <= count; number += 1) {
          const page = await file.getPage(number);
          if (cancelled) return;
          const viewport = page.getViewport({ scale: SCALE });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          const context = canvas.getContext("2d");
          if (!context) throw new Error("canvas");
          holder?.appendChild(canvas);
          await page.render({ canvasContext: context, viewport }).promise;
        }
        if (!cancelled) setCut(file.numPages > count);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      void task.destroy();
      holder?.replaceChildren();
    };
  }, [data]);

  return (
    <>
      <div ref={ref} className="file-preview-pages" />
      {failed ? <p className="error-text">{t("files.preview_failed")}</p> : null}
      {cut ? <p className="muted small">{t("files.preview_first_pages", { count: MAX_PAGES })}</p> : null}
    </>
  );
}
