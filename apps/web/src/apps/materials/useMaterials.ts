import { useCallback, useEffect, useRef, useState } from "react";
import { loadLibrary, deleteLibraryItem } from "../../api/textbooks";
import { listChapters, deleteChapter } from "../../api/chapters";
import { listChapterCourseware } from "../../api/courseware";
import type { components } from "../../types/generated/api";
import type { LibraryItem } from "../../types/textbook";
import type { ChapterSummary } from "../../types/chapter";

export type MaterialCourseware = components["schemas"]["CoursewareDocumentSummary"];

/** Keep each collection usable when the other cannot be loaded; ignore stale refreshes. */
export function useMaterials() {
  const [uploads, setUploads] = useState<LibraryItem[]>([]);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [courseware, setCourseware] = useState<Record<string, MaterialCourseware[]>>({});
  const [coursewareErrors, setCoursewareErrors] = useState<string[]>([]);
  const [coursewareLoading, setCoursewareLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadsAvailable, setUploadsAvailable] = useState(false);
  const [chaptersAvailable, setChaptersAvailable] = useState(false);
  const request = useRef({ version: 0 });
  const reload = useCallback(async () => {
    const id = ++request.current.version;
    setLoading(true);
    setCoursewareLoading(true);
    const [files, courses] = await Promise.allSettled([loadLibrary(), listChapters()]);
    if (request.current.version !== id) return;
    const failures: string[] = [];
    if (files.status === "fulfilled") { setUploads(files.value); setUploadsAvailable(true); }
    else failures.push("已上传教材暂时无法读取，请重试。");
    if (courses.status === "fulfilled") { setChapters(courses.value); setChaptersAvailable(true); }
    else failures.push("课程暂时无法读取，请重试。");
    setErrors(failures);
    setLoading(false);
    if (courses.status === "fulfilled") {
      const tutorials = courses.value.filter((item) => item.teachingMode === "tutorial");
      const results = await Promise.allSettled(tutorials.map((item) => listChapterCourseware(item.chapterId)));
      if (request.current.version !== id) return;
      const documents: Record<string, MaterialCourseware[]> = {};
      const failed: string[] = [];
      results.forEach((result, index) => {
        const chapterId = tutorials[index].chapterId;
        if (result.status === "fulfilled") documents[chapterId] = result.value.items;
        else failed.push(chapterId);
      });
      setCourseware(documents);
      setCoursewareErrors(failed);
    }
    setCoursewareLoading(false);
  }, []);
  useEffect(() => { const tracker = request.current; void reload(); return () => { tracker.version++; }; }, [reload]);
  const remove = async (kind: "upload" | "course", id: string) => {
    if (kind === "upload") await deleteLibraryItem(id);
    else await deleteChapter(id);
    request.current.version++;
    setLoading(false);
    setCoursewareLoading(false);
    if (kind === "upload") setUploads((items) => items.filter((item) => item.uploadId !== id));
    else setChapters((items) => items.filter((item) => item.chapterId !== id));
  };
  return { uploads, chapters, courseware, coursewareErrors, coursewareLoading, errors, loading, uploadsAvailable, chaptersAvailable, reload, remove };
}
