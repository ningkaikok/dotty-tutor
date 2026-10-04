import { useEffect, useState } from "react";
import { parse } from "../../api/client";
import { loadBackgroundJob, retryBackgroundJob, cancelBackgroundJob } from "../../api/textbooks";
import type { BackgroundJob } from "../../types/textbook";

export interface AutomaticCoursesResult {
  chapters: Array<{ chapterId: string; title: string; pageStart: number; pageEnd: number }>;
  notices: string[];
  chapterLimit: number;
}

/** Follow a durable preparation job; retries reuse the same upload and chapter records. */
export function useAutomaticCourses(uploadId: string) {
  const [job, setJob] = useState<BackgroundJob<AutomaticCoursesResult> | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setError("");
    setJob(null);
    const poll = async (current: BackgroundJob<AutomaticCoursesResult>) => {
      if (stopped) return;
      setJob(current);
      if (current.status === "queued" || current.status === "running") {
        timer = setTimeout(() => {
          loadBackgroundJob<AutomaticCoursesResult>(current.jobId).then(poll).catch(fail);
        }, 800);
      }
    };
    const fail = (reason: unknown) => { if (!stopped) setError(reason instanceof Error ? reason.message : "课程制作失败"); };
    fetchResponse().then((response) => parse<BackgroundJob<AutomaticCoursesResult>>(response)).then(poll).catch(fail);
    function fetchResponse() {
      return fetch(`/api/chapters/from-upload/${encodeURIComponent(uploadId)}`, { method: "POST" });
    }
    return () => { stopped = true; clearTimeout(timer); };
  }, [uploadId, attempt]);
  const retry = async () => {
    try {
      if (job?.status === "failed" || job?.status === "cancelled") await retryBackgroundJob(job.jobId);
      setAttempt((value) => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "重试失败"); }
  };
  const cancel = async () => {
    if (!job) return;
    try { setJob(await cancelBackgroundJob<AutomaticCoursesResult>(job.jobId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "取消失败"); }
  };
  return { job, error, retry, cancel };
}
