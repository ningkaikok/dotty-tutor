import { useCallback, useEffect, useRef, useState } from "react";
import { loadLibrary } from "../../api/textbooks";
import { listChapters } from "../../api/chapters";
import type { LibraryItem } from "../../types/textbook";
import type { ChapterSummary } from "../../types/chapter";

/** Keep each collection usable when the other cannot be loaded; ignore stale refreshes. */
export function useMaterials() {
  const [uploads, setUploads] = useState<LibraryItem[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const request = useRef({ version: 0 });
  const reload = useCallback(async () => {
    const id = ++request.current.version;
    setLoading(true);
    const [files, courses] = await Promise.allSettled([loadLibrary(), listChapters()]);
    if (request.current.version !== id) return;
    const failures: string[] = [];
    if (files.status === "fulfilled") setUploads(files.value);
    else failures.push("已上传教材暂时无法读取，请重试。");
    if (courses.status === "fulfilled") setChapters(courses.value);
    else failures.push("课程暂时无法读取，请重试。");
    setErrors(failures);
    setLoading(false);
  }, []);
  useEffect(() => { const tracker = request.current; void reload(); return () => { tracker.version++; }; }, [reload]);
  return { uploads, chapters, errors, loading, reload };
}
