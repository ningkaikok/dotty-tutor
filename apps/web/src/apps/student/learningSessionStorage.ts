import type { LearningSession } from "../../types/lesson";

export interface LearningSessionScope {
  learnerId: string;
  publicationId: string;
  assignmentId?: string;
}

/**
 * Build a learner-scoped pointer. Legacy keys intentionally are not read: a
 * key that did not encode identity cannot be safely attributed after a
 * student switch, so it is treated as stale local state.
 */
export function learningSessionStorageKey(scope: LearningSessionScope): string {
  const activityId = scope.assignmentId || scope.publicationId;
  return `dotty-learning-session:${encodeURIComponent(scope.learnerId)}:${encodeURIComponent(activityId)}`;
}

export function isLearningSessionForScope(
  session: Pick<LearningSession, "learnerId" | "publicationId" | "assignmentId">,
  scope: LearningSessionScope,
): boolean {
  return session.learnerId === scope.learnerId
    && session.publicationId === scope.publicationId
    && (session.assignmentId ?? undefined) === (scope.assignmentId ?? undefined);
}
