import { useEffect, useState } from "react";
import { loadCoursewarePresentation, loadPublishedCourseware, submitCoursewareAttempt } from "../../api/courseware";
import { protectedSession, useLearnerId } from "../../api/identity";
import { studentPlaybackDocument, teacherPlaybackDocument, type CoursewarePresentationDocument } from "./coursewarePresentation";

/** Fetch the role-specific projection and persist only learner-scoped practice. */
export function useCoursewarePresentation(coursewareId: string, publicationId: string | undefined, role: "teacher" | "student") {
  const learnerId = useLearnerId();
  const [document, setDocument] = useState<CoursewarePresentationDocument | null>(null);
  const [error, setError] = useState("");
  const [busyActivity, setBusyActivity] = useState("");
  const actor = protectedSession();
  const playbackScope = role === "student"
    ? `student:${actor?.role === "student" && actor.learnerId ? actor.learnerId : learnerId}`
    : `teacher:${actor?.role === "teacher" ? "protected" : "demo"}`;

  useEffect(() => {
    const controller = new AbortController();
    setDocument(null);
    setError("");
    const load = role === "student"
      ? loadPublishedCourseware(coursewareId, publicationId, controller.signal).then(studentPlaybackDocument)
      : loadCoursewarePresentation(coursewareId, publicationId, controller.signal).then(teacherPlaybackDocument);
    load.then((value) => { if (!controller.signal.aborted) setDocument(value); }).catch((requestError: unknown) => {
      if (controller.signal.aborted) return;
      setError(requestError instanceof Error ? requestError.message : "课件暂时无法打开");
    });
    return () => controller.abort();
  }, [coursewareId, publicationId, role]);

  const submitPractice = role === "student" && document ? async (activityId: string, response: { text?: string; selectedOptions?: string[] }) => {
    setBusyActivity(activityId);
    try {
      const attempt = await submitCoursewareAttempt(coursewareId, {
        activityId,
        publicationId: document.publicationId,
        commandId: crypto.randomUUID(),
        response,
      });
      return { assessment: attempt.assessment, feedback: attempt.feedback };
    } finally {
      setBusyActivity("");
    }
  } : undefined;

  return { document, error, busyActivity, playbackScope, submitPractice };
}
