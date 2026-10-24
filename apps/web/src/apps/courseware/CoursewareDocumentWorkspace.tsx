import { CoursewareAiReviewPanel } from "./CoursewareAiReviewPanel";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
import { useCoursewareAiReview } from "./useCoursewareAiReview";
import { CoursewareMediaPanel } from "./CoursewareMediaPanel";
import { useNavigate } from "react-router";
import { CoursewareDraftEditor } from "./CoursewareDraftEditor";
import { useCoursewareDocumentWorkflow } from "./useCoursewareDocumentWorkflow";
import { Badge, Button, ButtonLink, PageHeader } from "../../components/ui";
import { CoursewarePlayer } from "./CoursewarePlayer";
import { draftPlaybackDocument } from "./coursewarePresentation";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { coursewareDocumentStatus } from "./coursewareDocumentStatus";
import { coursewareSubjectLabel } from "./coursewareWorkspaceView";
import { useRef, useState } from "react";

/** Compose the teacher editor from the version-bound document workflow. */
export function CoursewareDocumentWorkspace({ coursewareId }: { coursewareId: string }) {
  const navigate = useNavigate();
  const [draftDirty, setDraftDirty] = useState(false);
  const [mediaOpen, setMediaOpen] = useState(false);
  const [selectedPageId, setSelectedPageId] = useState<string>();
  const editorDetails = useRef<HTMLDetailsElement>(null);
  const { document, loading, busy, error, notice, reload, savePage, regenerate, review, publish, retryAutomatic } = useCoursewareDocumentWorkflow(coursewareId);
  const aiReview = useCoursewareAiReview(coursewareId, document?.recordVersion);
  const openPage = (pageId: string) => {
    if (draftDirty) return;
    setSelectedPageId(pageId);
    if (editorDetails.current) editorDetails.current.open = true;
  };
  const backTo = document ? `/studio/courseware?chapterId=${encodeURIComponent(document.chapterId)}` : "/studio/courseware";
  const header = <MaterialsHeader backTo={backTo} backLabel="返回所属章节" subtitle="课件管理" />;
  if (loading) return <main className="courseware-route-state app-shell">{header}<PageHeader className="courseware-workflow-header" title="正在读取课件" description="正在读取课件内容、发布状态和教材来源。" /><p role="status">请稍候…</p></main>;
  if (!document) return <main className="courseware-route-state app-shell">{header}<PageHeader className="courseware-workflow-header" title="课件暂不可用" description="请重新读取课件，或返回教材库。" /><p role="alert">{error || "找不到这份课件，或当前账号无权读取。"}</p><Button type="button" onClick={() => void reload()}>重新读取课件</Button><ButtonLink to="/studio">返回教材库</ButtonLink></main>;
  const status = coursewareDocumentStatus(document);
  const automaticBusy = status.kind === "making";
  const subject = coursewareSubjectLabel(document.subject);
  const pageDescription = `${subject} · ${document.durationMinutes} 分钟 · ${document.pages.length} 页 · 教材第 ${document.pageStart}–${document.pageEnd} 页`;
  const headerActions = <>
    {status.kind === "published" && document.publicationId && <ButtonLink variant="primary" to={`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`}>直接授课</ButtonLink>}
    {status.kind !== "published" && document.publicationId && <ButtonLink to={`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`}>查看已发布版本</ButtonLink>}
    {document.publicationId && document.sourceDistributionScope !== "private" && <ButtonLink to={`/learn/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`}>学生版</ButtonLink>}
  </>;

  return <main className="courseware-authoring-route app-shell">
    {header}
    <PageHeader className="courseware-workflow-header" eyebrow="课件详情" title={document.title} description={pageDescription} actions={headerActions} />
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}
    <section className="courseware-final-confirmation" aria-label="课件状态"><div><div className="courseware-document-title"><h2>发布状态</h2><Badge tone={status.tone}>{status.label}</Badge></div><p role="status">{status.message}</p>
      {notice && <p role="status">{notice}</p>}
      {draftDirty && <p role="status">请先保存当前页面修改，再继续发布。</p>}
      </div><div className="courseware-final-actions">
      {status.canPublish && <Button variant="primary" disabled={busy || draftDirty} onClick={() => void publish()}>{document.sourceDistributionScope === "private" ? "保存并使用" : "发布课件"}</Button>}
      </div>
    </section>
    {(status.kind === "needs-attention" || document.sourceReadiness !== "ready" || document.reviewIssues.length > 0 || document.generationStatus === "failed" || document.generationStatus === "partial") && <section className="courseware-job-status" aria-label="下一步操作">
      {document.automation?.retryable && <Button disabled={busy || draftDirty} onClick={() => void retryAutomatic()}>重试自动制作</Button>}
      {document.sourceReadiness !== "ready" && <p>教材来源不可用或需复核；请先回到教材来源检查原页、修订和分发范围。</p>}
      <Button type="button" disabled={busy || draftDirty} onClick={() => openPage(document.pages.find((page) => page.generationStatus === "failed" || page.error)?.pageId ?? document.pages[0]?.pageId ?? "")}>打开问题页与恢复操作</Button>
      <ButtonLink to={`/studio/chapters/${encodeURIComponent(document.chapterId)}`}>检查教材原页</ButtonLink>
      <Button type="button" disabled={busy || draftDirty} onClick={() => void reload()}>重新读取制作状态</Button>
    </section>}
    {document.generationStatus === "completed" && document.pages.length > 0 && <section className="courseware-document-preview" aria-label="课件预览"><CoursewarePlayer document={draftPlaybackDocument(document)} playbackScope={`teacher:draft:${coursewareId}`} displayMode="embedded" /></section>}
    <details ref={editorDetails}><summary>编辑页面与详细审核（可选）</summary>
    <CoursewareCoveragePanel chapterId={document.chapterId} revisionKey={document.recordVersion} sourceRevisionId={document.sourceRevisionId} />
    <CoursewareAiReviewPanel
      status={aiReview.status}
      error={aiReview.error}
      starting={aiReview.starting}
      busy={busy || draftDirty}
      onStart={() => void aiReview.start()}
      onReload={() => { void reload(); void aiReview.reload(); }}
      onOpenPage={draftDirty ? undefined : openPage}
      // Bulk confirmation is an ordinary teacher review action recorded under the teacher.
      onBulkConfirm={(pageIds) => void review("approve", pageIds, "教师批量确认：AI 初审未发现问题且未被抽检的页面").catch(() => undefined)}
    />
    <CoursewareDraftEditor
      document={document}
      busy={busy || automaticBusy}
      notice={notice}
      onSavePage={savePage}
      onRegeneratePage={regenerate}
      onReviewPage={(pageId, decision, note) => review(decision, [pageId], note)}
      onReviewDocument={review}
      onPublish={publish}
      showPublishAction={false}
      embedded
      onReload={reload}
      onPresent={() => document.publicationId && navigate(`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
      onLearn={() => document.publicationId && navigate(`/learn/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
      onDirtyChange={setDraftDirty}
      selectedPageId={selectedPageId}
      onSelectPage={setSelectedPageId}
    />
    </details>
    {document.publicationId && <details onToggle={(event) => setMediaOpen(event.currentTarget.open)}><summary>制作视频与语音（可选）</summary>{mediaOpen && <CoursewareMediaPanel coursewareId={coursewareId} publicationId={document.publicationId} />}</details>}
  </main>;
}
