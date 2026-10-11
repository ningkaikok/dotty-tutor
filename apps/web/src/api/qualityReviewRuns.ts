import { parse } from "./client";

export type QualityReviewModel = {
  provider: string;
  label: string;
  available: boolean;
  models: string[];
  detail: string;
};

export type QualityReviewArm = {
  arm: "A" | "B";
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  output: Record<string, unknown> | null;
  durationMs: number | null;
  promptTokens: number | null;
  outputTokens: number | null;
  cost: number | null;
  error: { message: string } | null;
  actualProvider?: string | null;
  actualModel?: string | null;
};

export type QualityReviewRunCase = {
  caseId: string;
  ordinal: number;
  recordVersion: number;
  sourceKind: string;
  sourceFingerprint: string;
  inputFingerprint: string;
  outputFingerprint: string;
  snapshot: {
    source: Record<string, unknown>;
    sourceLocator: Record<string, unknown>;
    sourcePage: number | null;
    input: Record<string, unknown>;
    expected: Record<string, unknown>;
    actual: Record<string, unknown>;
  };
  arms: Partial<Record<"A" | "B", QualityReviewArm>>;
};

export type QualityReviewRating = {
  caseId: string;
  actorId: string;
  actorKind: string;
  identityVerified: boolean;
  aPass: boolean;
  bPass: boolean;
  preferred: "A" | "B" | "tie" | "neither";
  rationale: string;
};

export type QualityReviewRun = {
  runId: string;
  taskInstruction: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  cancelRequested: boolean;
  perCallTimeoutSeconds: number;
  totalBudgetSeconds: number;
  maxOutputTokens: number;
  error: { message: string } | null;
  cases: QualityReviewRunCase[];
  ratings: QualityReviewRating[];
  blind: boolean;
  models: { baseline: { provider: string; model: string }; candidate: { provider: string; model: string } } | null;
};

export type QualityReviewRunCreate = {
  caseIds: string[];
  taskInstruction: string;
  baseline: { provider: string; model: string };
  candidate: { provider: string; model: string };
  perCallTimeoutSeconds: number;
  totalBudgetSeconds: number;
  maxOutputTokens: number;
};

export async function listQualityReviewRunModels(): Promise<QualityReviewModel[]> {
  const payload = await parse<{ providers?: QualityReviewModel[] }>(await fetch("/api/quality-review-runs/models", { cache: "no-store" }));
  return Array.isArray(payload.providers) ? payload.providers : [];
}

export async function listQualityReviewRuns(): Promise<QualityReviewRun[]> {
  const payload = await parse<{ items?: QualityReviewRun[] }>(await fetch("/api/quality-review-runs", { cache: "no-store" }));
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function getQualityReviewRun(runId: string): Promise<QualityReviewRun> {
  return parse<QualityReviewRun>(await fetch(`/api/quality-review-runs/${encodeURIComponent(runId)}`, { cache: "no-store" }));
}

export async function createQualityReviewRun(input: QualityReviewRunCreate): Promise<QualityReviewRun> {
  return parse<QualityReviewRun>(await fetch("/api/quality-review-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function cancelQualityReviewRun(runId: string): Promise<QualityReviewRun> {
  const payload = await parse<{ run: QualityReviewRun }>(await fetch(`/api/quality-review-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }));
  return payload.run;
}

export async function rateQualityReviewCase(
  runId: string,
  input: { caseId: string; aPass: boolean; bPass: boolean; preferred: QualityReviewRating["preferred"]; rationale: string },
): Promise<QualityReviewRun> {
  return parse<QualityReviewRun>(await fetch(`/api/quality-review-runs/${encodeURIComponent(runId)}/ratings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function getQualityReviewRunStatistics(runId: string): Promise<Record<string, unknown>> {
  return parse(await fetch(`/api/quality-review-runs/${encodeURIComponent(runId)}/summary`, { cache: "no-store" }));
}

export async function getQualityReviewGoldExport(): Promise<Record<string, unknown>> {
  return parse(await fetch("/api/quality-review-runs/gold-export", { cache: "no-store" }));
}
