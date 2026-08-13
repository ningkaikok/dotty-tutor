import type { CanvasAction } from "./question";
import type { ModelRun } from "./runtime";

/** 陪练只能沿这些可解释的阶段三状态前进，避免模型任意跳转流程。 */
export type TutorStage = "diagnose" | "explain" | "practice" | "verify";

export interface GuideContext {
  assessment?: "correct" | "partial" | "incorrect";
  assessmentAuthority?: "deterministic" | "guided";
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
