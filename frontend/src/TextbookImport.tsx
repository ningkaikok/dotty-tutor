import { PipelinePanel } from "./apps/textbook/import/PipelinePanel";
import { RuntimeSettings } from "./apps/textbook/import/RuntimeSettings";
import { TextbookLibrary } from "./apps/textbook/import/TextbookLibrary";
import { UploadPanel } from "./apps/textbook/import/UploadPanel";
import { useTextbookImport } from "./apps/textbook/import/useTextbookImport";
import type { TextbookImportResult } from "./types";

interface TextbookImportProps {
  onContinue: (result: TextbookImportResult) => void;
  onExit?: () => void;
}

/**
 * Page-level composition only. Upload orchestration lives in
 * useTextbookImport; each visual section is independently replaceable.
 */
export function TextbookImport({ onContinue, onExit }: TextbookImportProps) {
  const state = useTextbookImport({ onOpenLibraryItem: onContinue });

  return (
    <main className="import-shell">
      <header className="import-header">
        {onExit && <button className="route-back-button" onClick={onExit}>← 选择入口</button>}
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>内容生产工作台</span>
        </div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="import-intro">
        <span className="eyebrow">CONTENT STUDIO · 教材数字化</span>
        <h1>上传教材页或整本 PDF</h1>
        <p>此处面向内容生产者。大 PDF 会按 5 MB 断点上传，后端合并校验后每 5 页规划一个识别批次。</p>
      </section>

      <RuntimeSettings
        models={state.models}
        reviewModels={state.reviewModels}
        ocrProviders={state.ocrProviders}
        loading={state.runtimeLoading}
        phase={state.phase}
        onSelectModel={(provider, model) => void state.selectGenerationModel(provider, model)}
        onSelectReviewModel={(provider, model) => void state.selectReviewer(provider, model)}
        onSelectOcr={(provider) => void state.selectOcr(provider)}
      />

      <TextbookLibrary
        items={state.library}
        loadingId={state.libraryLoadingId}
        deletingId={state.deletingId}
        onOpen={(item) => void state.openLibraryItem(item)}
        onDelete={(item) => void state.removeLibraryItem(item)}
      />

      <section className="import-grid">
        <UploadPanel
          file={state.file}
          preview={state.preview}
          phase={state.phase}
          progress={state.progress}
          error={state.error}
          result={state.result}
          sourceText={state.sourceText}
          pdfMode={state.pdfMode}
          processingTask={state.processingTask}
          onChooseFile={state.chooseFile}
          onSourceTextChange={state.setSourceText}
          onUpload={() => void state.upload()}
          onPause={state.pause}
        />
        <PipelinePanel
          result={state.result}
          pdfMode={state.pdfMode}
          phase={state.phase}
          processingTask={state.processingTask}
          activeStage={state.processingStageIndex}
          onContinue={onContinue}
        />
      </section>
    </main>
  );
}
