import { useCallback, useEffect, useState } from "react";
import { editCoursewarePage, loadCourseware, publishCourseware, regenerateCoursewarePage, reviewCourseware } from "../../api/courseware";
import type { CoursewareDocument } from "../../api/courseware";
import { authoringDocument, type CoursewarePageEditView } from "./coursewareAuthoringView";
import { coursewareErrorMessage } from "./coursewareErrors";
import { retryBackgroundJob } from "../../api/textbooks";

/** Own version-bound requests and generation polling for the document editor. */
export function useCoursewareDocumentWorkflow(coursewareId: string) {
  const [document, setDocument] = useState<ReturnType<typeof authoringDocument> | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reload = useCallback(async () => {
    setError("");
    try {
      const current = await loadCourseware(coursewareId);
      setDocument(authoringDocument(current));
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "课件草稿暂时无法读取。"));
    } finally {
      setLoading(false);
    }
  }, [coursewareId]);

  useEffect(() => { setLoading(true); void reload(); }, [reload]);

  const automaticStatus = document?.automation?.status;
  const generationStatus = document?.generationStatus;
  useEffect(() => {
    const automatic = ["generating", "checking", "repairing"].includes(automaticStatus ?? "");
    if (!automatic && !["queued", "running"].includes(generationStatus ?? "")) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const current = await loadCourseware(coursewareId);
        if (!active) return;
        setDocument(authoringDocument(current));
        if (current.generationStatus === "queued" || current.generationStatus === "running" || ["generating", "checking", "repairing"].includes(current.automation?.status ?? "")) {
          timer = window.setTimeout(() => void poll(), 1500);
        }
      } catch (requestError) {
        if (!active) return;
        setError(coursewareErrorMessage(requestError, "检查逐页生成进度失败。"));
        timer = window.setTimeout(() => void poll(), 3000);
      }
    };
    timer = window.setTimeout(() => void poll(), 1200);
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [coursewareId, generationStatus, automaticStatus]);

  const update = async (action: () => Promise<CoursewareDocument>, success: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await action();
      setDocument(authoringDocument(updated));
      setNotice(success);
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "保存失败；请刷新后检查版本冲突。"));
      throw requestError;
    } finally {
      setBusy(false);
    }
  };

  const savePage = (pageId: string, page: CoursewarePageEditView) => {
    if (!document) return Promise.resolve();
    const version = document.pages.find((item) => item.pageId === pageId)?.version;
    if (!version) return Promise.reject(new Error("找不到当前页面版本，请刷新后重试。"));
    return update(() => editCoursewarePage(coursewareId, pageId, {
      expectedRecordVersion: document.recordVersion,
      page,
    }), "页面已保存；本页审核状态会重新待审。");
  };

  const regenerate = (pageId: string, reason: string) => {
    if (!document) return Promise.resolve();
    return update(async () => {
      const result = await regenerateCoursewarePage(coursewareId, pageId, {
        expectedRecordVersion: document.recordVersion,
        reason,
        commandId: crypto.randomUUID(),
      });
      return result.document;
    }, "已提交本页重做任务；其他已完成页面保持不变。");
  };

  const review = (decision: "approve" | "request_changes", pageIds: string[], note: string) => {
    if (!document) return Promise.resolve();
    return update(() => reviewCourseware(coursewareId, {
      decision,
      pageIds,
      note,
      expectedRecordVersion: document.recordVersion,
    }), decision === "approve" ? "教师审核结果已保存。" : "修改意见已提交。 ");
  };

  const publish = async () => {
    if (!document) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await publishCourseware(coursewareId, { expectedRecordVersion: document.recordVersion, confirmReview: true });
      setDocument(authoringDocument(await loadCourseware(coursewareId)));
      setNotice(`课件已发布，发布版本 ${result.version}。`);
    } catch (requestError) {
      // A timeout or malformed response can occur after publication committed.
      // Recover the persisted result before suggesting another publish action.
      try {
        const saved = await loadCourseware(coursewareId);
        if (saved.status === "published" && saved.publicationId && saved.publicationId !== document.publicationId) {
          setDocument(authoringDocument(saved));
          setNotice("课件已发布；已恢复保存成功的发布结果。");
          return;
        }
      } catch { /* Keep the original publish error if the persisted result is unavailable. */ }
      setError(coursewareErrorMessage(requestError, "课件发布失败；请查看来源和审核问题。"));
    } finally {
      setBusy(false);
    }
  };

  const retryAutomatic = async () => {
    if (!document?.automation?.retryable || !document.automation.jobId) return;
    setBusy(true); setError("");
    try { await retryBackgroundJob(document.automation.jobId); await reload(); }
    catch (requestError) { setError(coursewareErrorMessage(requestError, "自动制作暂时无法重试，请稍后重新读取。")); }
    finally { setBusy(false); }
  };

  return { document, loading, busy, error, notice, reload, savePage, regenerate, review, publish, retryAutomatic };
}
