import type { ModelCatalog, ModelProvider, OcrCatalog, OcrProvider } from "../../../types";
import type { UploadPhase } from "./useTextbookImport";

interface RuntimeSettingsProps {
  models: ModelCatalog | null;
  ocrProviders: OcrCatalog | null;
  loading: boolean;
  phase: UploadPhase;
  onSelectModel: (provider: ModelProvider, model: string) => void;
  onSelectOcr: (provider: OcrProvider) => void;
}

/** Model/OCR selection is intentionally isolated from upload state rendering. */
export function RuntimeSettings({
  models,
  ocrProviders,
  loading,
  phase,
  onSelectModel,
  onSelectOcr,
}: RuntimeSettingsProps) {
  const busy = loading || phase === "uploading" || phase === "processing";

  return (
    <section className="model-switcher panel">
      <div>
        <span className="eyebrow">MODEL RUNTIME</span>
        <strong>选择实际生成模型</strong>
        <small>默认优先本地模型；切换后会影响教材脚本和 Help 回答。</small>
      </div>
      <select
        value={models ? `${models.selected.provider}::${models.selected.model}` : ""}
        disabled={!models || busy}
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
        <span className={`runtime-status ${models.selected.provider}`}>
          <i /> {models.providers.find((item) => item.id === models.selected.provider)?.detail}
        </span>
      )}

      <div className="ocr-label">
        <strong>选择教材解析方式</strong>
        <small>MinerU 输出 Markdown、公式 LaTeX 和结构化内容，随后交给模型生成课程。</small>
      </div>
      <select
        className="ocr-select"
        value={ocrProviders?.selected ?? ""}
        disabled={!ocrProviders || busy}
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
