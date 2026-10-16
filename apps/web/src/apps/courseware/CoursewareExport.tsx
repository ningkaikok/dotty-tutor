import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { loadCoursewarePresentation, loadPublishedCourseware } from "../../api/courseware";
import { studentPlaybackDocument, teacherPlaybackDocument, type CoursewarePresentationDocument } from "./coursewarePresentation";
import { CoursewarePrintDocument } from "./CoursewarePlayer";
import { apiResourceUrl } from "../../api/client";
import { useLearnerId } from "../../api/identity";

/** Revalidate the fixed publication before every export; only teacher projection can include answers. */
export function CoursewareExport({ coursewareId, publicationId, role }: {
  coursewareId: string; publicationId: string; role: "teacher" | "student";
}) {
  const learnerId = useLearnerId();
  const scope = `${role}:${learnerId}:${coursewareId}:${publicationId}`;
  const [preview, setPreview] = useState<{ scope: string; document: CoursewarePresentationDocument; answers: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [printRequested, setPrintRequested] = useState(false);
  const request = useRef<AbortController | null>(null);
  const activePreview = preview?.scope === scope ? preview : null;

  useEffect(() => {
    setPreview(null);
    setBusy(false);
    setError("");
    setPrintRequested(false);
    return () => { request.current?.abort(); };
  }, [scope]);

  const load = async (answers: boolean, print = false) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const fetchSnapshot = () => answers && role === "teacher"
        ? loadCoursewarePresentation(coursewareId, publicationId, controller.signal).then(teacherPlaybackDocument)
        : loadPublishedCourseware(coursewareId, publicationId, controller.signal).then(studentPlaybackDocument);
      const document = await fetchSnapshot();
      if (controller.signal.aborted) return;
      // Image readiness is required before opening the native print dialog; a missing
      // source asset must be fixed, not silently exported as an empty placeholder.
      await Promise.all(document.assets.filter((asset) => asset.src).map((asset) => new Promise<void>((resolve, reject) => {
        const image = new Image();
        controller.signal.addEventListener("abort", () => { image.src = ""; reject(new DOMException("Aborted", "AbortError")); }, { once: true });
        image.onload = () => resolve();
        image.onerror = () => reject(new Error("教材图片未能完整读取，请检查来源后重试"));
        image.src = apiResourceUrl(asset.src!);
      })));
      await Promise.resolve(window.document.fonts?.ready);
      // Scope/source can be withdrawn while images or fonts are loading. Re-read
      // the same publication after those waits before revealing or printing it.
      await fetchSnapshot();
      if (controller.signal.aborted) return;
      setPreview({ scope, document, answers: answers && role === "teacher" });
      setPrintRequested(print);
    } catch (reason) {
      if (controller.signal.aborted) return;
      setPreview(null);
      setError(reason instanceof Error ? reason.message : "导出暂不可用，请重试");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  };

  useEffect(() => {
    if (!activePreview || !printRequested) return;
    let cancelled = false;
    void Promise.resolve(document.fonts?.ready).then(() => {
      if (cancelled) return;
      window.print();
      setPrintRequested(false);
    });
    return () => { cancelled = true; };
  }, [activePreview, printRequested]);

  return <>
    <button type="button" disabled={busy} onClick={() => void load(false)}>导出学生版 / PDF</button>
    {role === "teacher" && <button type="button" disabled={busy} onClick={() => void load(true)}>导出教师答案版</button>}
    {busy && <span role="status">正在核对发布版本与图片…</span>}
    {error && <p role="alert">{error}</p>}
    {activePreview && createPortal(<section className="courseware-print-root" aria-label="课件打印预览">
      <nav className="courseware-print-controls"><p>固定发布第 {activePreview.document.version} 版 · {activePreview.answers ? "教师答案版（含答案）" : "学生版（不含答案）"}。在打印窗口选择“另存为 PDF”。</p>
        <button type="button" disabled={busy} onClick={() => void load(activePreview.answers, true)}>打印 / 保存 PDF</button>
        <button type="button" onClick={() => { request.current?.abort(); setBusy(false); setPreview(null); setPrintRequested(false); }}>关闭打印预览</button>
      </nav><CoursewarePrintDocument document={activePreview.document} includeAnswers={role === "teacher" && activePreview.answers} />
    </section>, document.body)}
  </>;
}
