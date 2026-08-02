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

export type QuestionContentBlock = TextContentBlock | MathContentBlock | ImageContentBlock | OptionsContentBlock;

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
  contentBlocks?: QuestionContentBlock[];
  publicationStatus?: "ready" | "needs_review";
  sourceEvidence?: {
    questionNumber: string;
    sourceHash: string;
    imageReferences: string[];
  };
  sourceArtifactUrl?: string;
  promptArtifactUrl?: string;
  sourceBatchId?: string;
  sourcePages?: { start: number; end: number };
  visualContext?: Array<{ description: string; facts: string[]; conflicts: string[] }>;
}

export interface LessonStep {
  id: string;
  title: string;
  text: string;
  speechText: string;
  action: CanvasAction;
}

export interface QuestionPayload {
  question: Question;
  lessonSteps: LessonStep[];
  architecture: Record<string, string>;
  modelRun: ModelRun;
  review?: ReviewRun;
  quality?: QualityReport;
}

export interface ReviewRun {
  status: "reviewed" | "needs_review";
  needsHumanReview: boolean;
  text: {
    verdict: string;
    corrections: Array<{ field: string; original: string; corrected: string; reason: string }>;
    issues: string[];
    confidence: number;
  };
  vision: {
    correctAnswer?: string;
    imageAssessments: Array<{
      index: number;
      belongsToQuestion: boolean;
      visualDescription: string;
      relevantFacts: string[];
      conflicts: string[];
    }>;
    issues: string[];
    confidence: number;
  };
  textModelRun: ModelRun;
  visionModelRun: ModelRun;
}

export type ModelProvider = "ollama" | "codex" | "mock";

export interface ModelRun {
  requestedProvider: string;
  provider: string;
  model: string;
  fallback: boolean;
  error?: string;
}

export interface ModelCatalog {
  selected: { provider: ModelProvider; model: string };
  providers: Array<{
    id: ModelProvider;
    label: string;
    available: boolean;
    models: string[];
    detail: string;
  }>;
}

export type OcrProvider = "auto" | "mineru" | "pypdf";

export interface OcrRun {
  requestedProvider: string;
  provider: string;
  mode: string;
  fallback: boolean;
  output: string;
  error?: string;
  sourceArtifactUrl?: string;
  promptArtifactUrl?: string;
}

export interface OcrCatalog {
  selected: OcrProvider;
  effective: string;
  providers: Array<{
    id: OcrProvider;
    label: string;
    available: boolean;
    detail: string;
  }>;
}

export interface GuideContext {
  assessment?: "correct" | "partial" | "incorrect";
  stuckAt?: string;
  knowledge?: string[];
  hint?: string;
  question?: string;
}

export interface TutorReply {
  reply: string;
  guideContext: GuideContext;
  nextHintLevel: number;
  canvasAction: CanvasAction;
  source: "stored-guide-card" | "answer-check" | "model-generated";
  modelRun: ModelRun;
}

export interface ImportStage {
  id: string;
  label: string;
  status: "done";
}

export interface TextbookImportResult {
  uploadId?: string;
  importId: string;
  filename: string;
  contentType: string;
  size: number;
  stored: boolean;
  modelRun: ModelRun;
  ocrRun: OcrRun;
  reviewRun?: ReviewRun;
  stages: ImportStage[];
  extraction: {
    chapter: string;
    knowledgePoint: string;
    questionCount: number;
    questionLimit?: number;
    formulaCount: number;
    guideCardCount: number;
    pageCount?: number;
    batchCount?: number;
    confidence: number;
    mode: string;
  };
  batches?: Array<{
    id: string;
    startPage: number;
    endPage: number;
    pageCount: number;
    status: "processed" | "queued" | "failed";
    error?: string;
  }>;
  questionPayload: QuestionPayload;
  questionPayloads?: QuestionPayload[];
}

export interface LibraryItem {
  uploadId: string;
  importId: string;
  filename: string;
  size: number;
  status: "complete";
  questionCount: number;
  pageCount?: number;
  chapter: string;
  updatedAt: number;
}

export interface BatchProcessResult {
  batch: NonNullable<TextbookImportResult["batches"]>[number];
  questionPayload: QuestionPayload;
  questionPayloads?: QuestionPayload[];
  ocrRun: OcrRun;
  modelRun: ModelRun;
  modelRuns?: ModelRun[];
  reviewRun?: ReviewRun;
  reviewRuns?: ReviewRun[];
}

export interface PdfUploadTask {
  uploadId: string;
  filename: string;
  size: number;
  chunkSize: number;
  totalChunks: number;
  uploadedChunks: number[];
  status: "uploading" | "merging" | "validating" | "splitting" | "ocr" | "generating" | "complete" | "failed";
  progress: number;
  message: string;
  elapsedSeconds: number;
  result?: TextbookImportResult;
}
