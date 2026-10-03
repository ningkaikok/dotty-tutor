import { useState } from "react";
import { loadChapterAttemptForReview, reviewChapterAttempt } from "../../api/chapters";
import type { ChapterAttemptResult } from "../../types/chapter";

export function useChapterAttemptReview(chapterId: string) {
  const [result, setResult] = useState<ChapterAttemptResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (request: (attemptId: string) => Promise<ChapterAttemptResult>, attemptId: string, fallback: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await request(attemptId);
      setResult(next);
      return next;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : fallback);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const read = (attemptId: string) => run((id) => loadChapterAttemptForReview(chapterId, id), attemptId, "作答读取失败");
  const review = (attemptId: string, decision: "correct" | "incorrect", note: string) => run(
    (id) => reviewChapterAttempt(chapterId, id, { reviewer: "teacher", decision, note }),
    attemptId,
    "教师复核失败",
  );

  return { result, busy, error, read, review };
}
