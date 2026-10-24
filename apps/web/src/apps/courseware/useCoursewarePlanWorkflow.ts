import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { ApiRequestError } from "../../api/client";
import { listChapters, loadChapter } from "../../api/chapters";
import { loadLibrary, loadBackgroundJob, retryBackgroundJob } from "../../api/textbooks";
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
  const sourceJobId = params.get("sourceJobId") ?? "";
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [profilesError, setProfilesError] = useState("");
  const [uploads, setUploads] = useState<LibraryItem[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [upload, setUpload] = useState<LibraryItem | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [chaptersError, setChaptersError] = useState("");
  const [chapter, setChapter] = useState<ChapterManagement | null>(null);
  const [planLoaded, setPlanLoaded] = useState(false);
  const [plan, setPlan] = useState<CoursewarePlanView | null>(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [documentsError, setDocumentsError] = useState("");
  const [uploadDocuments, setUploadDocuments] = useState<Record<string, DocumentSummary[]>>({});
  const [uploadDocumentErrors, setUploadDocumentErrors] = useState<string[]>([]);
  const [uploadDocumentsLoading, setUploadDocumentsLoading] = useState(false);
  const [job, setJob] = useState<JobView | null>(null);
  const [jobPurpose, setJobPurpose] = useState<JobPurpose | null>(null);
  const [preparedChapters, setPreparedChapters] = useState<PreparedChapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [audience, setAudience] = useState("依据教材内容与难度适配的学习者");
  const [languagePolicy, setLanguagePolicy] = useState("中文讲解；保留教材原文、术语与公式，英语例句保留英文");
  const [durationMinutes, setDurationMinutes] = useState(40);
  const [objectives, setObjectives] = useState("根据所选教材原页提炼并覆盖核心概念、示例与学习目标");
  const [pageStart, setPageStart] = useState("");
  const [pageEnd, setPageEnd] = useState("");
  // Supplementing opens a new plan form over an approved plan; its lessons are retained server-side.
  const [supplementing, setSupplementing] = useState(false);
  const [baseRefreshVersion, setBaseRefreshVersion] = useState(0);
  const workspaceRequest = useRef(0);
  const uploadDocumentsRequest = useRef(0);

  const loadUploadCourseware = useCallback(async (id: string, relatedChapters: ChapterSummary[], signal?: AbortSignal) => {
    const requestId = ++uploadDocumentsRequest.current;
    const targets = relatedChapters.filter((item) => item.uploadId === id && item.teachingMode === "tutorial");
    setUploadDocuments({});
    setUploadDocumentErrors([]);
    setUploadDocumentsLoading(true);
    try {
      const results = await Promise.allSettled(targets.map((item) => listChapterCourseware(item.chapterId, signal)));
      if (signal?.aborted || requestId !== uploadDocumentsRequest.current) return;
      const byChapter: Record<string, DocumentSummary[]> = {};
      const failed: string[] = [];
      results.forEach((result, index) => {
        const chapterId = targets[index].chapterId;
        if (result.status === "fulfilled") byChapter[chapterId] = result.value.items;
        else failed.push(chapterId);
      });
      setUploadDocuments(byChapter);
      setUploadDocumentErrors(failed);
    } finally {
      if (!signal?.aborted && requestId === uploadDocumentsRequest.current) setUploadDocumentsLoading(false);
    }
  }, []);

  const reloadUploadCourseware = useCallback(() => {
    if (uploadId) return loadUploadCourseware(uploadId, chapters);
  }, [chapters, loadUploadCourseware, uploadId]);
  const reloadMaterialLists = useCallback(() => setBaseRefreshVersion((version) => version + 1), []);

  const sourceCompleted = useCallback((result: unknown) => {
    const found = preparedChapterList(result);
    setPreparedChapters(found);
    if (found.length === 1 && result && typeof result === "object" && "planJobId" in result) {
      setParams({ chapterId: found[0].chapterId, mode: "automatic" });
    }
    if (!found.length) setError("来源任务已完成，但没有可用章节结果；请重新读取教材库。");
  }, [setParams]);

  const refreshWorkspace = useCallback(async (id: string) => {
    const requestId = ++workspaceRequest.current;
    const isCurrent = () => requestId === workspaceRequest.current;
    setLoading(true);
    setError("");
    setChapter(null);
    setPlan(null);
    setPlanLoaded(false);
    setSupplementing(false);
    setJob(null);
    setJobPurpose(null);
    setDocuments([]);
    setDocumentsLoaded(false);
    setDocumentsError("");
    try {
      const loadedChapter = await loadChapter(id);
      if (!isCurrent()) return;
      setChapter(loadedChapter);
      const [planResult, documentResult] = await Promise.allSettled([loadCoursewarePlan(id), listChapterCourseware(id)]);
      if (!isCurrent()) return;
      if (documentResult.status === "fulfilled") {
        setDocuments(documentResult.value.items);
        setDocumentsLoaded(true);
        setDocumentsError("");
      } else {
        setDocuments([]);
        setDocumentsLoaded(false);
        setDocumentsError(coursewareErrorMessage(documentResult.reason, "课件列表暂时无法读取。"));
      }
      if (planResult.status === "fulfilled") {
        const current = planView(planResult.value);
        setPlan(current);
        setAudience(current.audience);
        setLanguagePolicy(current.languagePolicy);
        setPageStart(String(current.pageStart));
        setPageEnd(String(current.pageEnd));
        setPlanLoaded(true);
        if (current.generationJobId) {
          const resumed = await loadBackgroundJob(current.generationJobId);
          if (!isCurrent()) return;
          setJob(resumed);
          if (resumed.status === "failed") setError(typeof resumed.lastError?.message === "string" ? resumed.lastError.message : "自动制作未能完成，请重试或调整所选范围。");
          setJobPurpose("plan");
        }
      } else if (!(planResult.reason instanceof ApiRequestError && planResult.reason.status === 404)) {
        throw planResult.reason;
      } else {
        const source = loadedChapter.sourceRevisions[loadedChapter.sourceRevisions.length - 1];
        const shortSource = source && source.pageEnd - source.pageStart < 30;
        setPageStart(shortSource ? String(source.pageStart) : "");
        setPageEnd(shortSource ? String(source.pageEnd) : "");
        setPlanLoaded(true);
      }
    } catch (requestError) {
      if (isCurrent()) setError(coursewareErrorMessage(requestError, "教材章节暂时无法读取。"));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setProfilesLoading(true);
    setProfilesLoaded(false);
    setProfilesError("");
    setUploadError("");
    setChaptersError("");
    setUpload(null);
    setUploads([]);
    setChapters([]);
    Promise.allSettled([listCoursewareProfiles(controller.signal), loadLibrary(), listChapters()]).then(([profileResult, uploadResult, chapterResult]) => {
      if (controller.signal.aborted) return;
      if (profileResult.status === "fulfilled") {
        setProfiles(profileResult.value.items);
        setProfilesLoading(false);
        setProfilesLoaded(true);
        setProfilesError("");
      } else {
        const message = coursewareErrorMessage(profileResult.reason, "学科配置暂时无法读取。");
        setProfilesLoading(false);
        setProfilesLoaded(false);
        setProfilesError(message);
        setError(message);
      }
      if (uploadResult.status === "fulfilled") {
        setUploads(uploadResult.value);
        const selectedUpload = uploadResult.value.find((item) => item.uploadId === uploadId) ?? null;
        setUpload(selectedUpload);
        setUploadError(uploadId && !selectedUpload ? "找不到所选上传文件；文件可能已删除或当前账号无权读取。" : "");
      } else {
        const message = coursewareErrorMessage(uploadResult.reason, "教材库暂时无法读取。");
        setUploadError(message);
        if (uploadId) setError(message);
      }
      if (chapterResult.status === "fulfilled") {
        setChapters(chapterResult.value);
        setChaptersError("");
      } else {
        setChapters([]);
        setChaptersError(coursewareErrorMessage(chapterResult.reason, "教材章节暂时无法读取。"));
      }
      setLoading(false);
    });
    return () => controller.abort();
  }, [baseRefreshVersion, uploadId]);

  useEffect(() => {
    if (!uploadId || loading) {
      if (!uploadId) {
        uploadDocumentsRequest.current += 1;
        setUploadDocuments({});
        setUploadDocumentErrors([]);
        setUploadDocumentsLoading(false);
      }
      return;
    }
    const controller = new AbortController();
    void loadUploadCourseware(uploadId, chapters, controller.signal);
    return () => controller.abort();
  }, [chapters, loadUploadCourseware, loading, uploadId]);

  useEffect(() => {
    if (chapterId) {
      void refreshWorkspace(chapterId);
      return;
    }
    workspaceRequest.current += 1;
    setChapter(null);
    setPlan(null);
    setPlanLoaded(false);
    setSupplementing(false);
    setDocuments([]);
    setDocumentsLoaded(false);
    setDocumentsError("");
    setJob(null);
    setJobPurpose(null);
  }, [chapterId, uploadId, refreshWorkspace]);

  useEffect(() => {
    if (!uploadId || !sourceJobId) return;
    let active = true;
    void loadBackgroundJob(sourceJobId).then((resumed) => {
      if (!active) return;
      setJob(resumed); setJobPurpose("source");
      if (resumed.status === "succeeded") sourceCompleted(resumed.result);
      if (resumed.status === "failed" || resumed.status === "cancelled") setError(resumed.lastError?.message || resumed.message || "来源准备未能完成。");
    }).catch((requestError) => {
      if (active) setError(coursewareErrorMessage(requestError, "来源准备进度暂时无法恢复。"));
    });
    return () => { active = false; };
  }, [sourceJobId, uploadId, sourceCompleted]);

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
            sourceCompleted(next.result);
          } else if (chapterId) {
            await refreshWorkspace(chapterId);
            setNotice(params.get("mode") === "automatic" ? "教学大纲已自动校验，教程页面正在制作；完成后打开预览即可。" : "教材规划已生成，请核对目标、页码覆盖和逐页大纲。");
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
  }, [chapterId, job, jobPurpose, refreshWorkspace, sourceCompleted, params]);

  const producingDocuments = documents.some((item) => ["queued", "running"].includes(item.generationStatus) || ["generating", "checking", "repairing"].includes(item.automation?.status ?? ""));
  useEffect(() => {
    if (!chapterId || !producingDocuments) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const current = await listChapterCourseware(chapterId);
        if (active) setDocuments(current.items);
      } catch { /* Keep the existing lessons visible during a temporary read failure. */ }
      if (active) timer = window.setTimeout(() => void poll(), 2000);
    };
    timer = window.setTimeout(() => void poll(), 1200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [chapterId, producingDocuments]);

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
      setParams({ uploadId: upload.uploadId, sourceJobId: next.jobId, ...(input.autoGenerate ? { mode: "automatic" } : {}) });
      setNotice(input.autoGenerate ? "已开始自动制作；系统会准备原页、生成规划和教程，无需分别批准来源与大纲。" : "已提交教材来源准备任务；任务只建立章节来源，不生成课件页面。");
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

  const startPlan = async (automatic = true) => {
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
        autoGeneratePages: automatic,
        autoPublish: automatic,
      });
      setPlan(planView(result.plan));
      setSupplementing(false);
      if (result.job) {
        setJob(result.job);
        setJobPurpose("plan");
        setNotice(automatic ? "已开始自动制作，检查和修复完成后即可直接打开教程。" : "已提交规划生成任务；页面生成要等你确认规划后才会开始。");
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
  const clearWorkflow = () => {
    workspaceRequest.current += 1;
    setJob(null); setJobPurpose(null); setPreparedChapters([]); setPlan(null); setPlanLoaded(false); setSupplementing(false);
    setDocuments([]); setDocumentsLoaded(false); setDocumentsError(""); setChapter(null); setUpload(null); setError(""); setNotice("");
  };

  const retryAutomatic = async () => {
    if (!chapter || !plan) return;
    setBusy(true); setError("");
    try {
      const next = await createCoursewarePlan(chapter.chapterId, {
        subject: chapter.subject, audience: plan.audience, languagePolicy: plan.languagePolicy,
        durationMinutes: 20, objectives: plan.requestedObjectives?.length ? plan.requestedObjectives : ["根据教材原页覆盖核心概念、示例与学习目标"],
        expectedRecordVersion: chapter.recordVersion, pageStart: plan.pageStart, pageEnd: plan.pageEnd,
        supplement: false, autoGeneratePages: true, autoPublish: true,
      });
      setPlan(planView(next.plan)); setJob(next.job ?? null); setJobPurpose("plan");
      setNotice("已重试自动制作，继续复用已准备的教材来源。");
    } catch (requestError) { setError(coursewareErrorMessage(requestError, "自动制作重试未能启动。")); }
    finally { setBusy(false); }
  };

  const retrySource = async () => {
    if (!job || jobPurpose !== "source" || job.status !== "failed") return;
    setBusy(true); setError("");
    try {
      // Retry the persisted job so checkpointed original pages and the original
      // source identity are reused instead of creating another preparation task.
      setJob(await retryBackgroundJob(job.jobId));
      setNotice("已重试原来源任务；继续使用原页码范围和已识别页面。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "来源任务重试未能启动，请重新读取状态。"));
    } finally { setBusy(false); }
  };

  const retryPlan = async () => {
    if (!job || jobPurpose !== "plan" || job.status !== "failed" || !plan || plan.status === "approved") return;
    setBusy(true); setError("");
    try {
      setJob(await retryBackgroundJob(job.jobId));
      setNotice("已重试原规划任务；沿用原教材范围与生成设置，已完成课时保持不变。");
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "规划任务重试未能启动，请重新读取状态。"));
    } finally { setBusy(false); }
  };

  return {
    retryAutomatic,
    retrySource,
    retryPlan,
    automaticGeneration: params.get("mode") === "automatic" || Boolean(plan?.generationAudit?.automaticGeneration),
    uploadId, chapterId, profiles, profilesLoading, profilesLoaded, profilesError, uploads, chapters, upload, uploadError, chaptersError, chapter, plan, documents,
    documentsLoaded, documentsError, uploadDocuments, uploadDocumentErrors, uploadDocumentsLoading, reloadUploadCourseware, reloadMaterialLists,
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
