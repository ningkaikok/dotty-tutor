import type { MistakeEvidence, VariationExercise } from "../types/practice";
import { parse } from "./client";
import { currentLearnerId } from "./identity";

export async function listVariations(
  mistakeId: string,
  learnerId: string = currentLearnerId(),
): Promise<VariationExercise[]> {
  const result = await parse<{ items: VariationExercise[] }>(
    await fetch(`/api/mistakes/${mistakeId}/variations?learnerId=${encodeURIComponent(learnerId)}`, {
      cache: "no-store",
    }),
  );
  return result.items;
}

export async function createVariation(
  mistakeId: string,
  learnerId: string = currentLearnerId(),
): Promise<VariationExercise> {
  return parse<VariationExercise>(
    await fetch(`/api/mistakes/${mistakeId}/variations?learnerId=${encodeURIComponent(learnerId)}`, {
      method: "POST",
    }),
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

export async function loadMistakeEvidence(
  mistakeId: string,
  learnerId: string = currentLearnerId(),
): Promise<MistakeEvidence> {
  return parse<MistakeEvidence>(
    await fetch(`/api/mistakes/${mistakeId}/evidence?learnerId=${encodeURIComponent(learnerId)}`, {
      cache: "no-store",
    }),
  );
}
