import type { CanvasAction } from "./question";
import type { ModelRun } from "./runtime";

/**
 * 确定性判题提供给学生的事实集合；刻意不包含标准答案或期望值内容。
 *
 * 后端接口暂以宽松对象传输，前端组件在消费前仍会做运行时校验；这个联合类型
 * 只负责让各 strategy 的字段在已确认结构内保持可读、可复用。
 */
export type EvaluationEvidence =
  | {
      strategy: "choice-set-match";
      submittedLabels: string[];
      expectedCount: number;
      evaluatorVersion?: string;
    }
  | {
      strategy: "fill-blank-parts";
      totalBlanks: number;
      matchedCount: number;
      failedBlankIds: string[];
      evaluatorVersion?: string;
    }
  | {
      strategy: "numeric-tolerance";
      submittedRaw: string;
      tolerance: number;
      expectedCount: number;
      evaluatorVersion?: string;
    }
  | {
      strategy: "short-answer-text-match";
      submittedRaw: string;
      expectedCount: number;
      evaluatorVersion?: string;
    }
  | {
      strategy: "line-connections";
      submittedCount: number;
      requiredCount: number;
      evaluatorVersion?: string;
    }
  | {
      strategy: "sub-question-parts";
      parts: Array<{
        subQuestionId: string;
        status: "correct" | "incorrect" | "tutor" | "incomplete" | "ungraded";
        feedbackRequired?: boolean;
      }>;
      gradableCount: number;
      matchedCount: number;
      ungradedCount: number;
      complete: boolean;
      masteryEligible: boolean;
      evaluatorVersion?: string;
    };

/** 陪练只能沿这些可解释的阶段三状态前进，避免模型任意跳转流程。 */
export type TutorStage = "diagnose" | "explain" | "practice" | "verify";

export interface GuideContext {
  assessment?: "correct" | "partial" | "incorrect";
  assessmentAuthority?: "deterministic" | "guided";
  evaluationEvidence?: EvaluationEvidence;
  /** 逐小问判定摘要；tutor-only 小问不会获得 mastery 资格。 */
  evaluationSummary?: {
    strategy: string;
    parts: Array<{ subQuestionId: string; status: string; feedbackRequired?: boolean }>;
    gradableCount: number;
    matchedCount: number;
    ungradedCount: number;
    complete: boolean;
    masteryEligible: boolean;
  };
  stuckAt?: string;
  knowledge?: string[];
  hint?: string;
  question?: string;
  /** 模型提出的误区只能作为待确认假设，不能替代确定性判题。 */
  misconception?: TutorMisconception;
}

export interface TutorReply {
  reply: string;
  guideContext: GuideContext;
  nextHintLevel: number;
  canvasAction: CanvasAction;
  source: "stored-guide-card" | "answer-check" | "model-generated";
  modelRun: ModelRun;
}

export interface TutorMessage {
  messageId: string;
  threadId: string;
  role: "student" | "assistant";
  content: string;
  inputMode: "text" | "structured";
  assessment?: "correct" | "partial" | "incorrect";
  action: Record<string, unknown>;
  modelRun: ModelRun | Record<string, never>;
  createdAt: number;
}

export interface TutorThread {
  threadId: string;
  mistakeId: string;
  learnerId: string;
  stage: TutorStage;
  summary: string;
  hintLevel: number;
  messageCount: number;
  messages?: TutorMessage[];
  createdAt: number;
  updatedAt: number;
}

/** 单轮计划由后端领域规则生成；模型只能负责表达，不能覆盖判题或阶段。 */
export interface TutorTurnPlan {
  intent: TutorStudentIntent;
  assessment: "correct" | "partial" | "incorrect";
  misconception: TutorMisconception;
  errorStrategy: { id: string; objective: string; reason: string };
  teachingAction: TutorTeachingAction;
  shouldRevealAnswer: boolean;
  suggestedStage: TutorStage;
  replySource: TutorReply["source"];
  evaluationEvidence?: EvaluationEvidence;
  audit: {
    assessmentAuthority: "deterministic" | "guided";
    stageAuthority: "tutor-turn-plan";
    misconceptionConfirmed: boolean;
    generationTeachingAction: TutorTeachingAction;
    teachingActionAdjusted: boolean;
    modelMayOverride: false;
  };
}

export type TutorStudentIntentId =
  | "submit-answer"
  | "confirm-ready"
  | "request-hint"
  | "request-explanation"
  | "check-step"
  | "challenge-answer"
  | "request-example"
  | "express-confusion"
  | "off-topic";

export interface TutorStudentIntent {
  id: TutorStudentIntentId;
  confidence: number;
  evidence: string[];
}

export interface TutorMisconception {
  hypothesis: string;
  evidence: string;
  evidenceMatched: boolean;
  confidence: number;
  needsConfirmation: boolean;
}

export type TutorTeachingAction =
  | "extract-conditions"
  | "inspect-first-error"
  | "contrast-concepts"
  | "complete-step"
  | "show-micro-example"
  | "ask-justification"
  | "generate-micro-practice"
  | "run-self-check";

export interface TutorTurnResult {
  thread: TutorThread;
  reply: TutorReply;
  action: {
    type: "advance_stage" | "continue_stage";
    previousStage: TutorStage;
    nextStage: TutorStage;
    assessment: "correct" | "partial" | "incorrect";
    prompt: string;
    tutorTurnPlan: TutorTurnPlan;
    deduplication: {
      status: string;
      retryCount: 0 | 1;
      fallbackUsed: boolean;
      similarity?: number;
    };
    modelRun: Partial<ModelRun>;
  };
}
