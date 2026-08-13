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
        {onExit && <button className="route-back-button" onClick={onExit}>← 返回入口</button>}
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
        <p>此处面向内容生产者。可同时加入多个 PDF；每个文件独立断点上传、识别和展示进度，最多并行处理 3 个任务。</p>
      </section>

      <RuntimeSettings
        models={state.models}
        tutorModels={state.tutorModels}
        reviewModels={state.reviewModels}
        ocrProviders={state.ocrProviders}
        loading={state.runtimeLoading}
        phase={state.phase}
        onSelectModel={(provider, model) => void state.selectGenerationModel(provider, model)}
        onSelectTutorModel={(provider, model) => void state.selectTutor(provider, model)}
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
          uploads={state.uploads}
          activeUploadId={state.activeUploadId}
          phase={state.phase}
          error={state.error}
          sourceText={state.sourceText}
          onChooseFiles={state.chooseFiles}
          onSelectUpload={state.selectUpload}
          onRemoveUpload={state.removeUpload}
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
