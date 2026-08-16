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
