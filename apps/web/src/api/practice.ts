import type { MistakeEvidence, VariationExercise } from "../types/practice";
import { parse } from "./client";

export async function listVariations(mistakeId: string): Promise<VariationExercise[]> {
  const result = await parse<{ items: VariationExercise[] }>(
    await fetch(`/api/mistakes/${mistakeId}/variations`, { cache: "no-store" }),
  );
  return result.items;
}

export async function createVariation(mistakeId: string): Promise<VariationExercise> {
  return parse<VariationExercise>(
    await fetch(`/api/mistakes/${mistakeId}/variations`, { method: "POST" }),
  );
}

export async function answerVariation(
  variationId: string,
  input: { attemptId?: string; content: string; interactionResult: Record<string, unknown> },
): Promise<VariationExercise> {
  return parse<VariationExercise>(await fetch(`/api/variations/${variationId}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function loadMistakeEvidence(mistakeId: string): Promise<MistakeEvidence> {
  return parse<MistakeEvidence>(await fetch(`/api/mistakes/${mistakeId}/evidence`, { cache: "no-store" }));
}
