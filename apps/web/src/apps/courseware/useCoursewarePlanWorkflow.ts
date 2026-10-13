import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { ApiRequestError } from "../../api/client";
import { listChapters, loadChapter } from "../../api/chapters";
import { loadLibrary, loadBackgroundJob } from "../../api/textbooks";
import { approveCoursewarePlan, createCoursewarePlan, editCoursewarePlan, listChapterCourseware, listCoursewareProfiles, loadCoursewarePlan, prepareCoursewareSource } from "../../api/courseware";
import type { components } from "../../types/generated/api";
import type { ChapterManagement, ChapterSummary } from "../../types/chapter";
import type { LibraryItem } from "../../types/textbook";
import type { CoursewareSourcePreparationInput } from "./CoursewareSourcePrepare";
import { planView, type CoursewarePlanEditView, type CoursewarePlanView } from "./coursewarePlanView";
import type { CompletionRange } from "./coursewareCompletionView";
import { coursewareErrorMessage } from "./coursewareErrors";

type Profile = components["schemas"]["CoursewareProfileView"];
type DocumentSummary = components["schemas"]["CoursewareDocumentSummary"];
type JobView = { jobId: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; progress: number; message: string; result?: unknown; lastError?: Record<string, unknown> | null };
type PreparedChapter = { chapterId: string; title: string; subject: string; sourceRevisionId: string; recordVersion: number; pageStart: number; pageEnd: number };
type JobPurpose = "source" | "plan";

/** Owns route-backed loading, optimistic versioning, and resumable source/plan background jobs. */
export function useCoursewarePlanWorkflow() {
  const [params, setParams] = useSearchParams();
  const uploadId = params.get("uploadId") ?? "";
  const chapterId = params.get("chapterId") ?? "";
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [uploads, setUploads] = useState<LibraryItem[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [upload, setUpload] = useState<LibraryItem | null>(null);
  const [chapter, setChapter] = useState<ChapterManagement | null>(null);
  const [planLoaded, setPlanLoaded] = useState(false);
  const [plan, setPlan] = useState<CoursewarePlanView | null>(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [job, setJob] = useState<JobView | null>(null);
  const [jobPurpose, setJobPurpose] = useState<JobPurpose | null>(null);
  const [preparedChapters, setPreparedChapters] = useState<PreparedChapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [audience, setAudience] = useState("");
  const [languagePolicy, setLanguagePolicy] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(40);
  const [objectives, setObjectives] = useState("");
  const [pageStart, setPageStart] = useState("");
  const [pageEnd, setPageEnd] = useState("");
  // Supplementing opens a new plan form over an approved plan; its lessons are retained server-side.
  const [supplementing, setSupplementing] = useState(false);

  const refreshWorkspace = useCallback(async (id: string) => {
    setLoading(true);
    setError("");
    setChapter(null);
    setPlan(null);
    setPlanLoaded(false);
    setSupplementing(false);
    try {
      const loadedChapter = await loadChapter(id);
      setChapter(loadedChapter);
      const [planResult, documentResult] = await Promise.allSettled([loadCoursewarePlan(id), listChapterCourseware(id)]);
      if (planResult.status === "fulfilled") {
        const current = planView(planResult.value);
        setPlan(current);
        setAudience(current.audience);
        setLanguagePolicy(current.languagePolicy);
        setPageStart(String(current.pageStart));
        setPageEnd(String(current.pageEnd));
        setPlanLoaded(true);
      } else if (!(planResult.reason instanceof ApiRequestError && planResult.reason.status === 404)) {
        throw planResult.reason;
      } else {
        setPageStart("");
        setPageEnd("");
        setPlanLoaded(true);
      }
      if (documentResult.status === "fulfilled") setDocuments(documentResult.value.items);
      else setDocuments([]);
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "教材章节暂时无法读取。"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.allSettled([listCoursewareProfiles(controller.signal), loadLibrary(), listChapters()]).then(([profileResult, uploadResult, chapterResult]) => {
      if (controller.signal.aborted) return;
      setProfilesLoaded(profileResult.status === "fulfilled");
      if (profileResult.status === "fulfilled") setProfiles(profileResult.value.items);
      else setError(coursewareErrorMessage(profileResult.reason, "学科配置暂时无法读取。"));
      if (uploadResult.status === "fulfilled") {
        setUploads(uploadResult.value);
        setUpload(uploadResult.value.find((item) => item.uploadId === uploadId) ?? null);
      } else if (uploadId) setError(coursewareErrorMessage(uploadResult.reason, "教材库暂时无法读取。"));
      if (chapterResult.status === "fulfilled") setChapters(chapterResult.value);
      setLoading(false);
    });
    return () => controller.abort();
  }, [uploadId]);

  useEffect(() => {
    if (chapterId) void refreshWorkspace(chapterId);
  }, [chapterId, refreshWorkspace]);

  useEffect(() => {
    if (!job?.jobId || !jobPurpose || isTerminal(job.status)) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await loadBackgroundJob<unknown>(job.jobId);
        if (!active) return;
        setJob(next);
        if (next.status === "succeeded") {
          if (jobPurpose === "source") {
            const found = preparedChapterList(next.result);
            setPreparedChapters(found);
            if (!found.length) setError("来源任务已完成，但没有可用章节结果；请重新读取教材库。");
          } else if (chapterId) {
            await refreshWorkspace(chapterId);
            setNotice("教材规划已生成，请核对目标、页码覆盖和逐页大纲。");
          }
          return;
        }
        if (next.status === "failed" || next.status === "cancelled") {
          const message = next.lastError?.message;
          setError(typeof message === "string" ? message : next.message || "后台任务未能完成。");
          return;
        }
        timer = window.setTimeout(() => void poll(), 1200);
      } catch (requestError) {
        if (!active) return;
        setError(coursewareErrorMessage(requestError, "读取后台进度失败。"));
        timer = window.setTimeout(() => void poll(), 2500);
      }
    };
    timer = window.setTimeout(() => void poll(), 500);
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [chapterId, job, jobPurpose, refreshWorkspace]);

  const startSourcePreparation = async (input: CoursewareSourcePreparationInput) => {
    if (!upload) return;
    setBusy(true);
    setError("");
    setNotice("");
    setPreparedChapters([]);
    try {
      const next = await prepareCoursewareSource(upload.uploadId, input);
      setJob(next);
      setJobPurpose("source");
      setNotice("已提交教材来源准备任务；任务只建立章节来源，不生成课件页面。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "教材来源准备未能启动。"));
    } finally {
      setBusy(false);
    }
  };

  const checkRange = (startValue: number | undefined, endValue: number | undefined, maxPages = 80) => {
    const source = chapter?.sourceRevisions[chapter.sourceRevisions.length - 1];
    if (!source || !Number.isInteger(startValue) || !Number.isInteger(endValue) || startValue === undefined || endValue === undefined) {
      return "请明确填写教材起始页和结束页。";
    }
    if (startValue < source.pageStart || endValue > source.pageEnd || endValue < startValue) {
      return `规划范围必须位于教材来源第 ${source.pageStart}–${source.pageEnd} 页内。`;
    }
    if (endValue - startValue + 1 > maxPages) return `单份课件规划最多处理 ${maxPages} 页，请缩小教材页码范围。`;
    return "";
  };

  const selectCompletionRange = (range: CompletionRange) => {
    if (!chapter || !planLoaded || busy) return;
    // Only a finished approved plan can be supplemented; any other plan must be finished first.
    if (plan && !(range.supplement && plan.status === "approved")) return;
    const rangeError = checkRange(range.pageStart, range.pageEnd, 30);
    if (rangeError) { setError(rangeError); return; }
    setSupplementing(range.supplement);
    setPageStart(String(range.pageStart));
    setPageEnd(String(range.pageEnd));
    if (range.supplement) setObjectives("");
    setNotice(range.supplement
      ? `已选择补齐教材原页 ${range.pageStart}–${range.pageEnd}；已确认的课时会保留。请填写本批学习目标，再生成规划供教师确认。`
      : `已选择教材原页 ${range.pageStart}–${range.pageEnd}；请核对下方规划范围、学习对象与目标，再生成规划供教师确认。`);
  };

  const cancelSupplement = () => {
    if (!plan) return;
    setSupplementing(false);
    setPageStart(String(plan.pageStart));
    setPageEnd(String(plan.pageEnd));
    setNotice("");
  };

  const startPlan = async () => {
    if (!chapter || !planLoaded || (plan && !supplementing)) return;
    const requested = objectives.split(/\n+/).map((item) => item.trim()).filter(Boolean);
    const start = pageStart.trim() ? Number(pageStart) : undefined;
    const end = pageEnd.trim() ? Number(pageEnd) : undefined;
    const rangeError = checkRange(start, end, 30);
    if (rangeError || !audience.trim() || !languagePolicy.trim() || requested.length === 0) {
      setError(rangeError || "请填写学习对象、授课语言策略和至少一个学习目标。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await createCoursewarePlan(chapter.chapterId, {
        subject: chapter.subject,
        audience: audience.trim(),
        languagePolicy: languagePolicy.trim(),
        durationMinutes,
        objectives: requested,
        pageStart: start,
        pageEnd: end,
        expectedRecordVersion: chapter.recordVersion,
        supplement: supplementing,
      });
      setPlan(planView(result.plan));
      setSupplementing(false);
      if (result.job) {
        setJob(result.job);
        setJobPurpose("plan");
        setNotice("已提交规划生成任务；页面生成要等你确认规划后才会开始。");
      } else setNotice("规划已生成，请核对目标、页码覆盖和逐页大纲。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "课件规划生成未能启动。"));
    } finally {
      setBusy(false);
    }
  };

  const savePlan = async (value: CoursewarePlanEditView) => {
    if (!chapter || !plan) return;
    const rangeError = checkRange(value.pageStart ?? undefined, value.pageEnd ?? undefined);
    if (rangeError) throw new Error(rangeError);
    setBusy(true);
    setError("");
    try {
      const updated = await editCoursewarePlan(chapter.chapterId, { ...value, expectedRecordVersion: plan.recordVersion });
      setPlan(planView(updated));
      setNotice("规划修改已保存。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "规划保存失败；请刷新后检查版本冲突。"));
      throw requestError;
    } finally {
      setBusy(false);
    }
  };

  const approvePlan = async () => {
    if (!chapter || !plan) return;
    const rangeError = checkRange(plan.pageStart, plan.pageEnd);
    if (rangeError) { setError(rangeError); return; }
    setBusy(true);
    setError("");
    try {
      const result = await approveCoursewarePlan(chapter.chapterId, {
        planVersion: plan.planVersion,
        expectedRecordVersion: plan.recordVersion,
        commandId: crypto.randomUUID(),
      });
      setPlan(planView(result.plan));
      // Append: lessons of retained batches stay listed next to the newly confirmed ones.
      const confirmed = result.documents.map((item) => ({ coursewareId: item.coursewareId, title: item.title, status: item.status, generationStatus: item.generationStatus, pageCount: item.pages?.length ?? 0, recordVersion: item.recordVersion, publicationId: item.publicationId }));
      setDocuments((current) => [...current.filter((item) => !confirmed.some((next) => next.coursewareId === item.coursewareId)), ...confirmed]);
      setNotice("规划已确认；每个课时已建立独立课件页面任务。可打开页面查看生成进度。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "确认规划失败；请刷新后检查版本冲突。"));
    } finally {
      setBusy(false);
    }
  };

  const chooseUpload = (id: string) => {
    clearWorkflow();
    setParams({ uploadId: id });
  };
  const chooseChapter = (id: string) => {
    clearWorkflow();
    setParams({ chapterId: id });
  };
  const clearWorkflow = () => { setJob(null); setJobPurpose(null); setPreparedChapters([]); setPlan(null); setPlanLoaded(false); setSupplementing(false); setDocuments([]); setChapter(null); setUpload(null); setError(""); setNotice(""); };

  return {
    uploadId, chapterId, profiles, profilesLoaded, uploads, chapters, upload, chapter, plan, documents,
    job, jobPurpose, preparedChapters, loading, busy, error, notice, audience, setAudience,
    languagePolicy, setLanguagePolicy, durationMinutes, setDurationMinutes, objectives, setObjectives,
    pageStart, setPageStart, pageEnd, setPageEnd, startSourcePreparation, startPlan, savePlan, approvePlan,
    chooseUpload, chooseChapter, planLoaded, selectCompletionRange, supplementing, cancelSupplement,
    reloadWorkspace: () => chapterId && refreshWorkspace(chapterId),
  };
}

function isTerminal(status: JobView["status"]) {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

function preparedChapterList(value: unknown): PreparedChapter[] {
  if (!value || typeof value !== "object" || !Array.isArray((value as { chapters?: unknown }).chapters)) return [];
  return (value as { chapters: unknown[] }).chapters.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const item = entry as Partial<PreparedChapter>;
    if (typeof item.chapterId !== "string" || typeof item.title !== "string" || typeof item.subject !== "string"
      || typeof item.sourceRevisionId !== "string" || typeof item.recordVersion !== "number"
      || typeof item.pageStart !== "number" || typeof item.pageEnd !== "number") return [];
    return [{ chapterId: item.chapterId, title: item.title, subject: item.subject, sourceRevisionId: item.sourceRevisionId, recordVersion: item.recordVersion, pageStart: item.pageStart, pageEnd: item.pageEnd }];
  });
}
