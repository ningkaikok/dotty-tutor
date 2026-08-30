import type { CanvasAction } from "./question";

export interface LessonStep {
  id: string;
  title: string;
  text: string;
  speechText: string;
  action: CanvasAction;
}

export interface DiagramLessonBlock {
  id: string;
  type: "diagram";
  title: string;
  payload: { renderer: "geometry"; action: CanvasAction; text: string; speechText: string };
}

export interface MarkdownLessonBlock {
  id: string;
  type: "markdown";
  title: string;
  payload: { markdown: string };
}

export interface FormulaLessonBlock {
  id: string;
  type: "formula";
  title: string;
  payload: { latex: string };
}

export interface AnimationLessonBlock {
  id: string;
  type: "animation";
  title: string;
  payload: { src: string; poster?: string; caption?: string };
}

export interface AnnotationLessonBlock {
  id: string;
  type: "annotation";
  title: string;
  payload: { text: string };
}

export interface QuizLessonBlock {
  id: string;
  type: "quiz";
  title: string;
  payload: { questionId: string };
}

export interface HintLessonBlock {
  id: string;
  type: "hint";
  title: string;
  payload: { level: number; hint: string; question?: string };
}

export type LessonBlock =
  | DiagramLessonBlock
  | MarkdownLessonBlock
  | FormulaLessonBlock
  | AnimationLessonBlock
  | AnnotationLessonBlock
  | QuizLessonBlock
  | HintLessonBlock;

export interface LessonDocument {
  lessonId: string;
  title: string;
  version: number;
  status: "draft" | "in_review" | "review" | "published" | "archived";
  sourceUploadId?: string;
  knowledgePoints: string[];
  blocks: LessonBlock[];
  questionPayload?: Record<string, unknown>;
  guideCards?: Array<Record<string, unknown>>;
}

export interface LearningSession {
  sessionId: string;
  learnerId: string;
  publicationId: string;
  assignmentId?: string | null;
  startedAt: number;
  /**
   * 服务端按题目保存的作答快照。学生端恢复试卷时只需要这个只读证据，
   * 不把模型回复或页面临时状态当成答案来源。
   */
  attempts?: ExerciseAttemptRecord[];
}

export interface ExerciseAttemptInput {
  attemptId: string;
  questionId: string;
  response: Record<string, unknown>;
  assessment: "correct" | "partial" | "incorrect";
  hintLevel: number;
  durationMs: number;
  createdAt: number;
}

/** 学习会话中已经落库的作答；response 保留结构化控件的原始值。 */
export interface ExerciseAttemptRecord extends ExerciseAttemptInput {
  attemptId: string;
  knowledgePointId?: string;
  /** 仅用于兼容旧服务端返回，不参与客户端提交或查找。 */
  knowledgePoint?: string;
}

export interface MasteryState {
  learnerId: string;
  knowledgePointId: string;
  /** 服务端解析出的展示名称；客户端不得用它作为身份键。 */
  knowledgePoint: string;
  score: number;
  rawScore: number;
  evidenceConfidence: number;
  evidenceCount: number;
  algorithmVersion: string;
  computedAt: number | null;
  attemptCount: number;
  correctCount: number;
  lastPracticedAt: number | null;
}
