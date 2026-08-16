import type { ModelCatalog, ModelProvider, OcrCatalog, OcrProvider, ReviewModelCatalog } from "../types/runtime";
import { GeneratedSuccess, parse } from "./client";

type ModelsResponse = ModelCatalog & GeneratedSuccess<"get_models_api_models_get">;
type SelectedModelsResponse = ModelCatalog & GeneratedSuccess<"select_model_api_models_select_post">;
type OcrResponse = OcrCatalog & GeneratedSuccess<"get_ocr_providers_api_ocr_get">;

/**
 * 运行时配置 API。
 *
 * 这些选择当前是后端进程级 Demo 状态，不应在学生页面调用；生产多租户版本需要把选择下沉到
 * 租户或任务上下文，不能依赖此处的全局下拉框。
 */

export async function loadModels(): Promise<ModelCatalog> {
  return parse<ModelsResponse>(await fetch("/api/models"));
}

export async function selectModel(provider: ModelProvider, model: string): Promise<ModelCatalog> {
  return parse<SelectedModelsResponse>(await fetch("/api/models/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  }));
}

export async function loadTutorModels(): Promise<ModelCatalog> {
  return parse<ModelCatalog>(await fetch("/api/tutor-models"));
}

export async function selectTutorModel(provider: ModelProvider, model: string): Promise<ModelCatalog> {
  return parse<ModelCatalog>(await fetch("/api/tutor-models/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  }));
}

export async function loadReviewModels(): Promise<ReviewModelCatalog> {
  return parse<ReviewModelCatalog>(await fetch("/api/review-models"));
}

export async function selectReviewModel(provider: ModelProvider, model: string): Promise<ReviewModelCatalog> {
  // 审核模型使用独立端点，切换它不会改变下一道题使用的生成模型。
  return parse<ReviewModelCatalog>(await fetch("/api/review-models/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  }));
}

export async function loadOcrProviders(): Promise<OcrCatalog> {
  return parse<OcrResponse>(await fetch("/api/ocr"));
}

export async function selectOcrProvider(provider: OcrProvider): Promise<OcrCatalog> {
  return parse<OcrCatalog>(await fetch("/api/ocr/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  }));
}
