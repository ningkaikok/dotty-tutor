export type AssignmentLearnerStatus = "not_started" | "in_progress" | "completed" | "overdue";

export interface ClassSummary {
  classId: string;
  name: string;
  subject: string;
  gradeBand: string;
  memberCount: number;
  createdAt: number;
  updatedAt: number;
}

export interface ClassMember {
  learnerId: string;
  displayName: string;
  joinedAt: number;
}

export interface AssignmentSummary {
  assignmentId: string;
  classId: string;
  publicationId: string;
  title: string;
  publicationTitle: string;
  className?: string | null;
  dueAt: number | null;
  status: "active" | "archived";
  lessonIds: string[];
  questionCount: number;
  createdAt: number;
  updatedAt: number;
  assignmentPlanId?: string | null;
}

export interface AssignmentPlanGoal {
  planningTopicKey: string;
  topic: string;
  priority: number;
  objective: string;
  reason: string;
  evidenceRefs: string[];
}

export interface AssignmentPlan {
  planId: string;
  classId: string;
  publicationId: string;
  publicationVersion: number;
  sourceFingerprint: string;
  status: "draft" | "confirmed";
  result: {
    plannerVersion: string;
    fallback: boolean;
    fallbackReason: string | null;
    goals: AssignmentPlanGoal[];
    coverage: Array<{ planningTopicKey: string; topic: string; questionCount: number }>;
    mastery: Array<Record<string, unknown>>;
    errorStats: Array<Record<string, unknown>>;
    personalized?: boolean;
    sourcePlanId?: string;
    sourcePublicationId?: string;
    lessons?: Array<Record<string, unknown>>;
  };
  warnings: Array<{ code: string; severity: string; message: string }>;
  assignmentId: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface ClassDetail extends ClassSummary {
  members: ClassMember[];
  assignments: AssignmentSummary[];
}

export interface StudentAssignment extends AssignmentSummary {
  learnerId: string;
  sessionId: string | null;
  attemptedCount: number;
  progress: number;
  learnerStatus: AssignmentLearnerStatus;
}

export interface DashboardStudent {
  learnerId: string;
  displayName: string;
  sessionId: string | null;
  attemptedCount: number;
  questionCount: number;
  progress: number;
  status: AssignmentLearnerStatus;
  averageMastery: number | null;
}

export interface KnowledgePointDashboard {
  knowledgePointId: string;
  knowledgePoint: string;
  observedStudentCount: number;
  averageScore: number | null;
  distribution: {
    notStarted: number;
    needsSupport: number;
    developing: number;
    mastered: number;
  };
  overriddenStudentCount: number;
  evidence: Array<{
    learnerId: string;
    displayName: string;
    questionId: string;
    assessment: "correct" | "partial" | "incorrect";
    reviewStatus: "unreviewed" | "reviewed" | "overturned";
    correctedAssessment: "correct" | "partial" | "incorrect" | null;
  }>;
}

export interface ClassDashboard {
  class: Pick<ClassSummary, "classId" | "name" | "subject" | "gradeBand">;
  assignment: AssignmentSummary;
  summary: {
    memberCount: number;
    startedCount: number;
    completedCount: number;
    completionRate: number | null;
  };
  students: DashboardStudent[];
  knowledgePoints: KnowledgePointDashboard[];
  reviewMetrics: {
    judgedCount: number;
    reviewedCount: number;
    overturnedCount: number;
    reviewRate: number | null;
    overturnRate: number | null;
    overrideCount: number;
  };
  metricDefinition: string;
}
