import type { ModelCatalog, ModelProvider, OcrCatalog, OcrProvider, ReviewModelCatalog } from "../../../types";
import type { RuntimeLoadingState, UploadPhase } from "./useTextbookImport";

interface RuntimeSettingsProps {
  models: ModelCatalog | null;
  tutorModels: ModelCatalog | null;
  reviewModels: ReviewModelCatalog | null;
  ocrProviders: OcrCatalog | null;
  loading: RuntimeLoadingState;
  phase: UploadPhase;
  onSelectModel: (provider: ModelProvider, model: string) => void;
  onSelectTutorModel: (provider: ModelProvider, model: string) => void;
  onSelectReviewModel: (provider: ModelProvider, model: string) => void;
  onSelectOcr: (provider: OcrProvider) => void;
}

/** 模型与 OCR 选择器独立于上传状态渲染，避免设置变化重置上传状态机。 */
export function RuntimeSettings({
  models,
  tutorModels,
  reviewModels,
  ocrProviders,
  loading,
  phase,
  onSelectModel,
  onSelectTutorModel,
  onSelectReviewModel,
  onSelectOcr,
}: RuntimeSettingsProps) {
  const uploadBusy = phase === "uploading" || phase === "processing";
  const disabledHint = uploadBusy ? "教材正在上传或识别，完成后可切换运行时" : undefined;

  return (
    <section className="model-switcher panel">
      <div>
        <span className="eyebrow">MODEL RUNTIME</span>
        <strong>选择实际生成模型</strong>
        <small>默认优先本地模型；切换后会影响教材脚本和 Help 回答。</small>
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

      <div className="tutor-label">
        <strong>选择错题陪练模型</strong>
        <small>独立于题目生成和审核；学生每轮对话会使用这里的模型。</small>
      </div>
      <select
        className="tutor-select"
        value={tutorModels ? `${tutorModels.selected.provider}::${tutorModels.selected.model}` : ""}
        disabled={!tutorModels || uploadBusy || loading.tutor}
        title={disabledHint}
        onChange={(event) => {
          const [provider, model] = event.target.value.split("::") as [ModelProvider, string];
          onSelectTutorModel(provider, model);
        }}
      >
        {!tutorModels && <option>正在读取陪练模型…</option>}
        {tutorModels?.providers.flatMap((provider) => provider.models.map((model) => (
          <option key={`tutor::${provider.id}::${model}`} value={`${provider.id}::${model}`} disabled={!provider.available}>
            {provider.label} · {model}
          </option>
        )))}
      </select>
      {tutorModels && (
        <span className={`runtime-status tutor-status ${tutorModels.selected.provider}`}>
          <i /> 当前陪练：{tutorModels.selected.provider} · {tutorModels.selected.model}
        </span>
      )}

      <div className="review-label">
        <strong>选择文字审核模型</strong>
        <small>审核模型独立于生成模型；教材事实、公式和单位建议使用能力更强的模型。</small>
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
  );
}
