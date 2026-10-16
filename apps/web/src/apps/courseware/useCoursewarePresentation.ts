import { useEffect, useRef, useState } from "react";
import { loadCoursewarePresentation, loadPublishedCourseware, submitCoursewareAttempt } from "../../api/courseware";
import { protectedSession, useLearnerId } from "../../api/identity";
import { studentPlaybackDocument, teacherPlaybackDocument, type CoursewarePresentationDocument } from "./coursewarePresentation";

/** Fetch the role-specific projection and persist only learner-scoped practice. */
export function useCoursewarePresentation(coursewareId: string, publicationId: string | undefined, role: "teacher" | "student") {
  const learnerId = useLearnerId();
  const scope = `${role}:${learnerId}:${coursewareId}:${publicationId ?? "latest"}`;
  const [presentation, setPresentation] = useState<{ scope: string; document: CoursewarePresentationDocument | null; error: string }>({ scope, document: null, error: "" });
  const [busy, setBusy] = useState<{ scope: string; activityId: string } | null>(null);
  const pendingCommands = useRef(new Map<string, string>());
  const actor = protectedSession();
  const playbackScope = role === "student"
    ? `student:${actor?.role === "student" && actor.learnerId ? actor.learnerId : learnerId}`
    : `teacher:${actor?.role === "teacher" ? "protected" : "demo"}`;

  useEffect(() => {
    const controller = new AbortController();
    setPresentation({ scope, document: null, error: "" });
    const load = role === "student"
      ? loadPublishedCourseware(coursewareId, publicationId, controller.signal).then(studentPlaybackDocument)
      : loadCoursewarePresentation(coursewareId, publicationId, controller.signal).then(teacherPlaybackDocument);
    load.then((value) => { if (!controller.signal.aborted) setPresentation({ scope, document: value, error: "" }); }).catch((requestError: unknown) => {
      if (controller.signal.aborted) return;
      setPresentation({ scope, document: null, error: requestError instanceof Error ? requestError.message : "课件暂时无法打开" });
    });
    return () => controller.abort();
  }, [coursewareId, publicationId, role, learnerId, scope]);

  // Route transitions can render before effects reset state; never hand an old
  // teacher projection or another learner's error to the newly selected scope.
  const document = presentation.scope === scope ? presentation.document : null;
  const error = presentation.scope === scope ? presentation.error : "";
  const busyActivity = busy?.scope === scope ? busy.activityId : "";

  const submitPractice = role === "student" && document ? async (activityId: string, response: { text?: string; selectedOptions?: string[] }) => {
    setBusy({ scope, activityId });
    const key = JSON.stringify([playbackScope, document.publicationId, activityId, response]);
    const commandId = pendingCommands.current.get(key) ?? crypto.randomUUID();
    pendingCommands.current.set(key, commandId);
    try {
      const attempt = await submitCoursewareAttempt(coursewareId, {
        activityId,
        publicationId: document.publicationId,
        commandId,
        response,
      });
      pendingCommands.current.delete(key);
      return { assessment: attempt.assessment, feedback: attempt.feedback };
    } finally {
      setBusy((previous) => previous?.scope === scope && previous.activityId === activityId ? null : previous);
    }
  } : undefined;

  return { document, error, busyActivity, playbackScope, submitPractice };
}
