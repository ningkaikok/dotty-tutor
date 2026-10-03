import { useCallback, useEffect, useState } from "react";
import {
  editChapterLesson,
  generateChapter,
  loadChapter,
  publishChapter,
  reviseChapter,
  reviewChapterLesson,
} from "../../api/chapters";
import { ApiRequestError } from "../../api/client";
import type { ChapterLesson, ChapterLessonEditInput, ChapterManagement, ChapterSource } from "../../types/chapter";

/** Uses record-version preconditions for edits and reviews so stale pages cannot approve changed lesson content. */
export function useChapterStudio(chapterId: string) {
  const [chapter, setChapter] = useState<ChapterManagement | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const next = await loadChapter(chapterId, signal);
      if (!signal?.aborted) setChapter(next);
      return next;
    } catch (requestError) {
      if (!signal?.aborted) setError(requestError instanceof Error ? requestError.message : "章节读取失败");
      return null;
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const mutate = async (key: string, action: () => Promise<ChapterManagement>, success: string) => {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const next = await action();
      setChapter(next);
      setNotice(success);
      return next;
    } catch (requestError) {
      if (requestError instanceof ApiRequestError && requestError.status === 409) {
        await reload();
        setError("章节已被其他人更新，当前内容已刷新；请重新核对来源和题目后再操作。");
      } else {
        setError(requestError instanceof Error ? requestError.message : "保存失败");
      }
      return null;
    } finally {
      setBusy("");
    }
  };

  const generate = () => mutate("generate", () => generateChapter(chapterId), "已生成课程草稿，请逐课复核。模板内容不会自动发布。");
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

  return { chapter, loading, busy, error, notice, reload, generate, revise, edit, review, publish };
}
