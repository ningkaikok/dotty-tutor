import "./apps/chapters/chapters.css";
import { Link, useSearchParams } from "react-router";
import { AppHeader, Badge, PageHeader } from "./components/ui";
import { PipelinePanel } from "./apps/textbook/import/PipelinePanel";
import { RuntimeSettings } from "./apps/textbook/import/RuntimeSettings";
import { TextbookLibrary } from "./apps/textbook/import/TextbookLibrary";
import { UploadPanel } from "./apps/textbook/import/UploadPanel";
import { useTextbookImport } from "./apps/textbook/import/useTextbookImport";
import type { TextbookImportResult } from "./types/index";
import { PRODUCT_TERMS } from "./productTerms";

interface TextbookImportProps {
  onContinue: (result: TextbookImportResult) => void;
  onExit?: () => void;
}

/**
 * Page-level composition only. Upload orchestration lives in
 * useTextbookImport; each visual section is independently replaceable.
 */
export function TextbookImport({ onContinue, onExit }: TextbookImportProps) {
  const [params] = useSearchParams();
  const state = useTextbookImport({ onOpenLibraryItem: onContinue, initialUploadId: params.get("uploadId") ?? "" });

  return (
    <main className="import-shell">
      <AppHeader className="materials-header import-header" subtitle="教材与试卷导入" backTo="/studio" onBack={onExit} backLabel="我的教材" actions={<div className="import-header-actions">
        <details className="import-tools"><summary>工具</summary><nav aria-label="内容制作工具">
          <Link to="/studio/prompts">教学策略与提示词</Link>
          <Link to="/studio/metrics">模型调用指标</Link>
          <Link to="/studio/dependency-preflight">环境依赖自检</Link>
        </nav></details>
        <Badge tone="neutral">{PRODUCT_TERMS.demoBadge}</Badge>
      </div>} />

      <PageHeader className="import-page-heading" eyebrow="教材数字化" title="上传试卷或教材"
        description="支持 PDF 与图片。试卷继续识别原题；教材识别后可选择学科与页码，进入课件制作流程。" />

      <details className="chapter-advanced"><summary>识别与模型设置</summary>
      <RuntimeSettings
        models={state.models}
        tutorModels={state.tutorModels}
        tutorEvaluation={state.tutorEvaluation}
        tutorEvaluationLoading={state.tutorEvaluationLoading}
        tutorEvaluationError={state.tutorEvaluationError}
        reviewModels={state.reviewModels}
        ocrProviders={state.ocrProviders}
        loading={state.runtimeLoading}
        phase={state.phase}
        onSelectModel={(provider, model) => void state.selectGenerationModel(provider, model)}
        onEvaluateTutorModel={(provider, model) => void state.evaluateTutorModel(provider, model)}
        onSelectTutorModel={(provider, model) => void state.selectTutor(provider, model)}
        onSelectReviewModel={(provider, model) => void state.selectReviewer(provider, model)}
        onSelectOcr={(provider) => void state.selectOcr(provider)}
      />

      </details>

      <details className="chapter-advanced"><summary>查看已有教材与恢复任务</summary>
      <TextbookLibrary
        items={state.library}
        loadingId={state.libraryLoadingId}
        deletingId={state.deletingId}
        onOpen={(item) => void state.openLibraryItem(item)}
        onDelete={(item) => void state.removeLibraryItem(item)}
      />

      </details>

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
          onCancelProcessing={state.cancelProcessing}
          onRetryProcessing={state.retryProcessing}
        />
        <PipelinePanel
          result={state.result}
          pdfMode={state.pdfMode}
          phase={state.phase}
          processingTask={state.processingTask}
          activeStage={state.processingStageIndex}
          activeFilename={state.activeUpload?.file.name ?? ""}
          onContinue={onContinue}
        />
      </section>
    </main>
  );
}
