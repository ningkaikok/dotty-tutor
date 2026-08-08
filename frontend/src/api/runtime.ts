import type { ModelCatalog, ModelProvider, OcrCatalog, OcrProvider } from "../types/runtime";
import { parse } from "./client";

export async function loadModels(): Promise<ModelCatalog> {
  return parse<ModelCatalog>(await fetch("/api/models"));
}

export async function selectModel(provider: ModelProvider, model: string): Promise<ModelCatalog> {
  return parse<ModelCatalog>(await fetch("/api/models/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  }));
}

export async function loadOcrProviders(): Promise<OcrCatalog> {
  return parse<OcrCatalog>(await fetch("/api/ocr"));
}

export async function selectOcrProvider(provider: OcrProvider): Promise<OcrCatalog> {
  return parse<OcrCatalog>(await fetch("/api/ocr/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  }));
}
