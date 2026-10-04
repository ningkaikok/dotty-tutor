import { useCallback, useEffect, useState } from "react";
import { createChapter, listChapters } from "../../api/chapters";
import type { ChapterManagement, ChapterSummary, ChapterSubject, ChapterSource } from "../../types/chapter";

export function useChapterStudioIndex(loadList = true) {
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [loadingChapters, setLoadingChapters] = useState(loadList);
  const [chapterListError, setChapterListError] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const reload = useCallback(async () => {
    setLoadingChapters(true);
    setChapterListError("");
    try {
      setChapters(await listChapters());
    } catch (requestError) {
      setChapterListError(requestError instanceof Error ? requestError.message : "章节列表读取失败");
    } finally {
      setLoadingChapters(false);
    }
  }, []);

  useEffect(() => { if (loadList) void reload(); }, [reload, loadList]);

  const create = async (value: { title: string; subject: ChapterSubject; source: ChapterSource }): Promise<ChapterManagement | null> => {
    setCreating(true);
    setCreateError("");
    try {
      return await createChapter(value);
    } catch (requestError) {
      setCreateError(requestError instanceof Error ? requestError.message : "章节创建失败");
      return null;
    } finally {
      setCreating(false);
    }
  };

  return { chapters, loadingChapters, chapterListError, creating, createError, reload, create };
}
