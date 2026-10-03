import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  editChapterLesson,
  cancelChapterAiJob,
  generateChapter,
  generateChapterAi,
  loadChapterAiJob,
  loadChapter,
  publishChapter,
  reviseChapter,
  reviewChapterLesson,
  retryChapterAiJob,
} from "../../api/chapters";
import { ApiRequestError } from "../../api/client";
import type { ChapterLesson, ChapterLessonEditInput, ChapterManagement, ChapterSource } from "../../types/chapter";
import type { BackgroundJob } from "../../types/textbook";

/** Uses record-version preconditions for edits and reviews so stale pages cannot approve changed lesson content. */
export function useChapterStudio(chapterId: string) {
  const [chapter, setChapter] = useState<ChapterManagement | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [aiJob, setAiJob] = useState<BackgroundJob | null>(null);
  const aiJobRequestRef = useRef(0);
  const activeChapterIdRef = useRef(chapterId);
  const restoredGenerationJobIdRef = useRef("");
  useLayoutEffect(() => { activeChapterIdRef.current = chapterId; }, [chapterId]);

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const next = await loadChapter(chapterId, signal);
      if (!signal?.aborted && activeChapterIdRef.current === chapterId) setChapter(next);
      return next;
    } catch (requestError) {
      if (!signal?.aborted && activeChapterIdRef.current === chapterId) setError(requestError instanceof Error ? requestError.message : "章节读取失败");
      return null;
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    aiJobRequestRef.current += 1;
    restoredGenerationJobIdRef.current = "";
    setChapter(null); setAiJob(null); setBusy(""); setError(""); setNotice("");
  }, [chapterId]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    if (!aiJob || (aiJob.status !== "queued" && aiJob.status !== "running")) return;
    let active = true;
    const requestId = ++aiJobRequestRef.current;
    const timer = window.setTimeout(() => {
      void loadChapterAiJob(aiJob.jobId).then((next) => {
        if (!active || requestId !== aiJobRequestRef.current || activeChapterIdRef.current !== chapterId) return;
        setAiJob(next);
        if (next.status === "succeeded") {
          void reload().then(() => { if (activeChapterIdRef.current === chapterId) setNotice("AI 草稿已生成，请逐课复核来源依据和内容。"); });
        } else if (next.status === "failed") {
          setError(next.lastError?.message || next.message || "AI 草稿生成失败，可重试该任务。");
        }
      }).catch((requestError) => {
        if (active && requestId === aiJobRequestRef.current && activeChapterIdRef.current === chapterId) {
          setError(requestError instanceof Error ? requestError.message : "读取 AI 生成进度失败");
          setAiJob((current) => current ? { ...current } : current);
        }
      });
    }, 900);
    return () => { active = false; window.clearTimeout(timer); };
  }, [aiJob, chapterId, reload]);

  useEffect(() => {
    const generationJobId = chapter?.chapterId === chapterId ? chapter.generationJobId : null;
    if (!generationJobId || restoredGenerationJobIdRef.current === generationJobId || aiJob?.jobId === generationJobId) return;
    let active = true;
    const requestId = ++aiJobRequestRef.current;
    restoredGenerationJobIdRef.current = generationJobId;
    void loadChapterAiJob(generationJobId).then((job) => {
      if (!active || requestId !== aiJobRequestRef.current || activeChapterIdRef.current !== chapterId) return;
      setAiJob(job);
      if (job.status === "succeeded") void reload();
      if (job.status === "failed") setError(job.lastError?.message || job.message || "AI 草稿生成失败，可重试该任务。");
    }).catch((requestError) => {
      if (active && requestId === aiJobRequestRef.current && activeChapterIdRef.current === chapterId) {
        setError(requestError instanceof Error ? requestError.message : "恢复 AI 草稿任务失败");
        restoredGenerationJobIdRef.current = "";
      }
    });
    return () => { active = false; };
  }, [aiJob?.jobId, chapter?.chapterId, chapter?.generationJobId, chapterId, reload]);

  const mutate = async (key: string, action: () => Promise<ChapterManagement>, success: string) => {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const next = await action();
      if (activeChapterIdRef.current === chapterId) { setChapter(next); setNotice(success); }
      return next;
    } catch (requestError) {
      if (activeChapterIdRef.current === chapterId && requestError instanceof ApiRequestError && requestError.status === 409) {
        await reload();
        setError("章节已被其他人更新，当前内容已刷新；请重新核对来源和题目后再操作。");
      } else if (activeChapterIdRef.current === chapterId) {
        setError(requestError instanceof Error ? requestError.message : "保存失败");
      }
      return null;
    } finally {
      if (activeChapterIdRef.current === chapterId) setBusy("");
    }
  };

  const generate = () => mutate("generate", () => generateChapter(chapterId), "已生成课程草稿，请逐课复核。模板内容不会自动发布。");
  const startAiGeneration = async () => {
    if (!chapter || aiJob && (aiJob.status === "queued" || aiJob.status === "running")) return;
    setBusy("generate-ai"); setError(""); setNotice("");
    const requestId = ++aiJobRequestRef.current;
    try {
      const job = await generateChapterAi(chapterId, chapter.recordVersion);
      if (requestId === aiJobRequestRef.current && activeChapterIdRef.current === chapterId) setAiJob(job);
      if (job.status === "failed") setError(job.lastError?.message || job.message || "AI 草稿生成失败，可重试该任务。");
      return job;
    } catch (requestError) {
      if (activeChapterIdRef.current !== chapterId) return null;
      if (requestError instanceof ApiRequestError && requestError.status === 409) {
        await reload(); setError("章节已更新，请刷新后重新核对来源，再生成 AI 草稿。");
      } else setError(requestError instanceof Error ? requestError.message : "无法启动 AI 草稿生成");
      return null;
    } finally { if (activeChapterIdRef.current === chapterId) setBusy(""); }
  };
  const retryAiGeneration = async () => {
    if (!aiJob) return;
    setBusy("generate-ai"); setError("");
    const requestId = ++aiJobRequestRef.current;
    try { const next = await retryChapterAiJob(aiJob.jobId); if (requestId === aiJobRequestRef.current && activeChapterIdRef.current === chapterId) setAiJob(next); }
    catch (requestError) { if (activeChapterIdRef.current === chapterId) setError(requestError instanceof Error ? requestError.message : "重试 AI 草稿失败"); }
    finally { if (activeChapterIdRef.current === chapterId) setBusy(""); }
  };
  const cancelAiGeneration = async () => {
    if (!aiJob) return;
    setBusy("generate-ai"); setError("");
    const requestId = ++aiJobRequestRef.current;
    try { const next = await cancelChapterAiJob(aiJob.jobId); if (requestId === aiJobRequestRef.current && activeChapterIdRef.current === chapterId) setAiJob(next); }
    catch (requestError) { if (activeChapterIdRef.current === chapterId) setError(requestError instanceof Error ? requestError.message : "取消 AI 草稿失败"); }
    finally { if (activeChapterIdRef.current === chapterId) setBusy(""); }
  };
  const revise = (source: ChapterSource) => mutate("revise", () => reviseChapter(chapterId, source, chapter?.recordVersion ?? 0), "来源已修订，相关草稿已标记为待复核；历史发布版本保留。 ");
  const edit = (lesson: ChapterLesson, value: ChapterLessonEditInput) => mutate(
    `edit:${lesson.lessonId}`,
    () => editChapterLesson(chapterId, lesson.lessonId, value, chapter?.recordVersion ?? 0),
    "检查题已保存，等待人工复核。",
  );
  const review = (lesson: ChapterLesson, decision: "approve" | "request_changes") => mutate(
    `review:${lesson.lessonId}`,
    () => reviewChapterLesson(chapterId, lesson.lessonId, { decision, reviewer: "teacher", note: "" }, chapter?.recordVersion ?? 0),
    decision === "approve" ? "已记录人工复核。" : "已标记待修改。",
  );
  const publish = async () => {
    setBusy("publish");
    setError("");
    setNotice("");
    try {
      const result = await publishChapter(chapterId);
      await reload();
      setNotice(`已发布课程版本 ${result.version}。`);
      return result;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "发布失败");
      return null;
    } finally {
      setBusy("");
    }
  };

  return { chapter, loading, busy, error, notice, aiJob, reload, generate, startAiGeneration, retryAiGeneration, cancelAiGeneration, revise, edit, review, publish };
}
