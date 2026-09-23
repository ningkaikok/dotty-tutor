export type ModelProvider = "ollama" | "codex" | "mock";

export interface ModelRun {
  requestedProvider: string;
  provider: string;
  model: string;
  fallback: boolean;
  error?: string;
  stages?: Array<{
    name: string;
    provider?: string;
    model?: string;
    fallback: boolean;
    cacheHit?: boolean;
    skipped?: boolean;
  }>;
}

export interface ModelCatalog {
  selected: { provider: ModelProvider; model: string };
  providers: Array<{
    id: ModelProvider;
    label: string;
    available: boolean;
    models: string[];
    detail: string;
    modelDetails?: ModelCapabilityDetails[];
  }>;
}

export interface ModelCapabilityDetails {
  name: string;
  displayName: string;
  roles: string[];
  capabilities: string[];
  contextWindow: number;
  latencyTier: string;
  costTier: string;
  fallback: string | null;
  health: { healthy: boolean; consecutiveFailures: number; lastFailureReason: string | null; lastFailureAt: string | null };
}

export interface TutorModelRef {
  provider: Exclude<ModelProvider, "mock">;
  model: string;
}

export interface TutorModelEvaluationArmSummary {
  cases: number;
  successfulCalls: number;
  failedCalls: number;
  schemaSuccessRate: number;
  strictReferenceCases: number;
  strictReferenceCaseRate: number;
  exactReferenceFields: number;
  referenceFields: number;
  exactReferenceFieldRate: number;
  latencyMs: { p50: number | null; p95: number | null };
  tokenUsage: {
    knownPromptCalls: number;
    promptTokens: number | null;
    knownOutputCalls: number;
    outputTokens: number | null;
  };
  dimensions: Record<string, { cases: number; successfulCalls: number; strictReferenceCases: number }>;
}

export interface TutorModelEvaluationResult {
  caseId: string;
  taskDimension: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  rubric: Record<string, unknown>;
  baseline: TutorModelEvaluationCall;
  candidate: TutorModelEvaluationCall;
}

export interface TutorModelEvaluationCall {
  status: "success" | "failed";
  output: Record<string, unknown> | null;
  durationMs: number | null;
  promptTokens: number | null;
  outputTokens: number | null;
  schemaFallback: boolean | null;
  errorType?: string;
  referenceFields: Record<string, boolean>;
}

export interface TutorModelEvaluation {
  runId: string;
  status: "queued" | "running" | "completed" | "failed";
  createdAt: string;
  dataset: string;
  datasetHash: string;
  totalCases: number;
  completedCases: number;
  baseline: TutorModelRef;
  candidate: TutorModelRef;
  summary: {
    baseline: TutorModelEvaluationArmSummary;
    candidate: TutorModelEvaluationArmSummary;
    pairedStrictReferenceDifference: Record<string, number>;
  } | null;
  statisticalNote: string;
  errorType?: string;
  error?: string;
  results?: TutorModelEvaluationResult[];
}

/** 文字和题图审核共用同一个裁判模型，保证审核结论来自同一上下文。 */
export type ReviewModelCatalog = ModelCatalog;

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
