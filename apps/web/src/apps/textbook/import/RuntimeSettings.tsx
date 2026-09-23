import type { ModelCatalog, ModelProvider, OcrCatalog, OcrProvider, ReviewModelCatalog, TutorModelEvaluation } from "../../../types/index";
import type { RuntimeLoadingState, UploadPhase } from "./useTextbookImport";
import { TutorModelEvaluationPanel } from "./TutorModelEvaluationPanel";
import { PRODUCT_TERMS } from "../../../productTerms";

interface RuntimeSettingsProps {
  models: ModelCatalog | null;
  tutorModels: ModelCatalog | null;
  tutorEvaluation: TutorModelEvaluation | null;
  tutorEvaluationLoading: boolean;
  tutorEvaluationError: string;
  reviewModels: ReviewModelCatalog | null;
  ocrProviders: OcrCatalog | null;
  loading: RuntimeLoadingState;
  phase: UploadPhase;
  onSelectModel: (provider: ModelProvider, model: string) => void;
  onEvaluateTutorModel: (provider: Exclude<ModelProvider, "mock">, model: string) => void;
  onSelectTutorModel: (provider: ModelProvider, model: string) => void;
  onSelectReviewModel: (provider: ModelProvider, model: string) => void;
  onSelectOcr: (provider: OcrProvider) => void;
}

/** 模型与 OCR 选择器独立于上传状态渲染，避免设置变化重置上传状态机。 */
export function RuntimeSettings({
  models,
  tutorModels,
  tutorEvaluation,
  tutorEvaluationLoading,
  tutorEvaluationError,
  reviewModels,
  ocrProviders,
  loading,
  phase,
  onSelectModel,
  onEvaluateTutorModel,
  onSelectTutorModel,
  onSelectReviewModel,
  onSelectOcr,
}: RuntimeSettingsProps) {
  const uploadBusy = phase === "uploading" || phase === "processing";
  const disabledHint = uploadBusy ? "教材正在上传或识别，完成后可切换运行时" : undefined;

  const summary = models && tutorModels && reviewModels && ocrProviders
    ? `运行时 · 生成 ${models.selected.provider}/${models.selected.model} · ${PRODUCT_TERMS.tutoring} ${tutorModels.selected.provider}/${tutorModels.selected.model} · 审核 ${reviewModels.selected.provider}/${reviewModels.selected.model} · 解析 ${ocrProviders.effective}`
    : "正在读取运行时配置…";

  return (
    <details className="collapse-drawer panel">
      <summary className="collapse-drawer-summary">
        <span>{summary}</span>
        <i className="drawer-caret" aria-hidden="true" />
      </summary>
      <section className="model-switcher">
        <div>
          <strong>选择实际生成模型</strong>
          <small>默认使用 Codex default；切换后会影响教材脚本和 Help 回答。</small>
        </div>
        <select
          value={models ? `${models.selected.provider}::${models.selected.model}` : ""}
          disabled={!models || uploadBusy || loading.generation}
          title={disabledHint}
          onChange={(event) => {
            const [provider, model] = event.target.value.split("::") as [ModelProvider, string];
            onSelectModel(provider, model);
          }}
        >
          {!models && <option>正在读取模型…</option>}
          {models?.providers.flatMap((provider) => provider.models.map((model) => (
            <option
              key={`${provider.id}::${model}`}
              value={`${provider.id}::${model}`}
              disabled={!provider.available}
            >
              {provider.label} · {model}
            </option>
          )))}
        </select>
        {models && (
          <span className={`runtime-status generation-status ${models.selected.provider}`}>
            <i /> {models.providers.find((item) => item.id === models.selected.provider)?.detail}
          </span>
        )}

        <TutorModelEvaluationPanel
          models={tutorModels}
          evaluation={tutorEvaluation}
          evaluationLoading={tutorEvaluationLoading}
          evaluationError={tutorEvaluationError}
          disabled={uploadBusy}
          selectionLoading={loading.tutor}
          onEvaluate={onEvaluateTutorModel}
          onApply={onSelectTutorModel}
        />

        <div className="review-label">
          <strong>选择审核模型</strong>
          <small>同一个审核模型同时检查文字、题干图和选项图；教材事实、公式和单位建议使用能力更强的模型。</small>
        </div>
        <select
          className="review-select"
          value={reviewModels ? `${reviewModels.selected.provider}::${reviewModels.selected.model}` : ""}
          disabled={!reviewModels || uploadBusy || loading.review}
          title={disabledHint}
          onChange={(event) => {
            const [provider, model] = event.target.value.split("::") as [ModelProvider, string];
            onSelectReviewModel(provider, model);
          }}
        >
          {!reviewModels && <option>正在读取审核模型…</option>}
          {reviewModels?.providers.flatMap((provider) => provider.models.map((model) => (
            <option
              key={`review::${provider.id}::${model}`}
              value={`${provider.id}::${model}`}
              disabled={!provider.available}
            >
              {provider.label} · {model}
            </option>
          )))}
        </select>
        {reviewModels && (
          <span className={`runtime-status review-status ${reviewModels.selected.provider}`}>
            <i /> 当前审核：{reviewModels.selected.provider} · {reviewModels.selected.model}
          </span>
        )}

        <div className="ocr-label">
          <strong>选择教材解析方式</strong>
          <small>MinerU 输出 Markdown、公式 LaTeX 和结构化内容，随后交给模型生成课程。</small>
        </div>
        <select
          className="ocr-select"
          value={ocrProviders?.selected ?? ""}
          disabled={!ocrProviders || uploadBusy || loading.ocr}
          title={disabledHint}
          onChange={(event) => onSelectOcr(event.target.value as OcrProvider)}
        >
          {!ocrProviders && <option>正在读取 OCR…</option>}
          {ocrProviders?.providers.map((provider) => (
            <option key={provider.id} value={provider.id} disabled={!provider.available}>
              {provider.label}{provider.available ? "" : " · 未安装"}
            </option>
          ))}
        </select>
        {ocrProviders && (
          <span className={`runtime-status ocr-status ${ocrProviders.effective}`}>
            <i /> 当前实际解析：{ocrProviders.effective} · {ocrProviders.providers.find((item) => item.id === ocrProviders.effective)?.detail}
          </span>
        )}
      </section>
    </details>
  );
}
