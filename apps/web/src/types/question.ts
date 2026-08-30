import type { LessonStep } from "./lesson";
import type { ModelRun, ReviewRun } from "./runtime";

/** 当前几何画布可执行的有限动作集合。 */
export type CanvasAction =
  | "show-base"
  | "show-point-p"
  | "show-triangles"
  | "show-bisector";

export interface TextContentBlock {
  id: string;
  type: "text";
  text: string;
  sourceOrder: number;
}

export interface MathContentBlock {
  id: string;
  type: "math";
  latex: string;
  display: boolean;
  sourceOrder: number;
}

export interface ImageContentBlock {
  id: string;
  type: "image";
  url: string;
  assetId: string;
  sourceReference: string;
  role: "stem";
  sourceOrder: number;
}

export interface OptionContentItem {
  label: string;
  contentBlocks: Array<TextContentBlock | MathContentBlock>;
  imageUrl?: string;
  assetId?: string;
}

export interface OptionsContentBlock {
  id: string;
  type: "options";
  items: OptionContentItem[];
  sourceOrder: number;
}

export interface TableCell {
  contentBlocks: Array<TextContentBlock | MathContentBlock>;
}

export interface TableRow {
  cells: TableCell[];
}

/** 题干中的统计表；由后端解析原始 `<table>` HTML 生成，保留原始位置。 */
export interface TableContentBlock {
  id: string;
  type: "table";
  rows: TableRow[];
  sourceOrder: number;
}

export type QuestionContentBlock =
  | TextContentBlock
  | MathContentBlock
  | ImageContentBlock
  | OptionsContentBlock
  | TableContentBlock;

export interface QualityReport {
  status: "ready" | "needs_review";
  errors: string[];
  warnings: string[];
  validatorVersion: string;
  validatedAt: number;
}

export interface InteractionPoint {
  id: string;
  label: string;
  x: number;
  y: number;
}

export interface QuestionInteraction {
  type: "none" | "draw-line";
  instruction: string;
  points: InteractionPoint[];
  requiredConnections: string[][];
}

export type QuestionType =
  | "choice"
  | "multi-select"
  | "true-false"
  | "short-answer"
  | "fill-blank"
  | "numeric"
  | "draw-line";

export interface SubQuestionEvaluation {
  mode: "deterministic" | "tutor";
  reason?: string | null;
}

export interface SubQuestion {
  id: string;
  label: string;
  prompt: string;
  questionType: QuestionType;
  evaluation: SubQuestionEvaluation;
  options?: string[] | null;
  correctAnswer?: string | null;
  correctAnswers?: string[] | null;
  blanks?: BlankSpec[] | null;
  answerSpec?: AnswerSpec | null;
  interaction?: QuestionInteraction | null;
  contentBlocks?: Array<TextContentBlock | MathContentBlock>;
}

export interface SubQuestionAnswer {
  text?: string;
  selectedOptions?: string[];
  blankAnswers?: Record<string, string>;
  numericAnswer?: string;
  connections?: Array<[string, string]>;
}

export interface BlankSpec {
  id: string;
  label: string;
  answerType: "text" | "numeric" | "expression";
  correctAnswers?: string[];
  tolerance?: number;
  unit?: string;
}

export interface AnswerSpec {
  answerType: "numeric" | "expression";
  expected: string;
  accepted?: string[];
  tolerance?: number;
  unit?: string;
}

/** 教材课程、互动试卷和错题陪练共享的稳定题目契约。 */
export interface Question {
  id: string;
  questionType?: QuestionType;
  selectionMode?: "single" | "multiple";
  chapter: string;
  knowledgePoint: string;
  questionNumber?: string;
  prompt: string;
  correctAnswer?: string;
  correctAnswers?: string[];
  blanks?: BlankSpec[];
  answerSpec?: AnswerSpec;
  interaction?: QuestionInteraction;
  givens: string[];
  options?: string[];
  imageUrls?: string[];
  optionImageUrls?: string[];
  /** OCR 来源顺序投影；多图选择题用它区分题干图和 A-D 选项图。 */
  imageManifest?: Array<{
    order: number;
    role: "stem" | "option";
    optionLabel: string | null;
    sourceReference: string;
    url: string;
  }>;
  contentBlocks: QuestionContentBlock[];
  publicationStatus?: "ready" | "needs_review";
  sourceEvidence?: {
    questionNumber: string;
    sourceHash: string;
    imageReferences: string[];
  };
  sourceArtifactUrl?: string;
  promptArtifactUrl?: string;
  sourceBatchId?: string;
  /** Stable OCR source identity; regenerated revisions keep this key. */
  sourceQuestionKey?: string;
  sourcePages?: { start: number; end: number };
  visualContext?: Array<{ description: string; facts: string[]; conflicts: string[] }>;
  /** Optional independent parts; each part owns its answer/evaluation boundary. */
  subQuestions?: SubQuestion[];
}

export interface QuestionPayload {
  question: Question;
  lessonSteps: LessonStep[];
  architecture: Record<string, string>;
  modelRun: ModelRun;
  review?: ReviewRun;
  quality?: QualityReport;
}
