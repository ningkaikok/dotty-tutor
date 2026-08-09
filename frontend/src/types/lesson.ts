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
  startedAt: number;
}

export interface ExerciseAttemptInput {
  attemptId: string;
  questionId: string;
  knowledgePoint: string;
  response: Record<string, unknown>;
  assessment: "correct" | "partial" | "incorrect";
  hintLevel: number;
  durationMs: number;
  createdAt: number;
}

export interface MasteryState {
  learnerId: string;
  knowledgePoint: string;
  score: number;
  attemptCount: number;
  correctCount: number;
  lastPracticedAt: number;
}
