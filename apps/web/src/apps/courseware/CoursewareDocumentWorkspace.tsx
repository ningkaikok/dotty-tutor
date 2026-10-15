import { CoursewareAiReviewPanel } from "./CoursewareAiReviewPanel";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
import { useCoursewareAiReview } from "./useCoursewareAiReview";
import { CoursewareMediaPanel } from "./CoursewareMediaPanel";
import { useNavigate } from "react-router";
import { CoursewareDraftEditor } from "./CoursewareDraftEditor";
import { useCoursewareDocumentWorkflow } from "./useCoursewareDocumentWorkflow";
import { Button, ButtonLink } from "../../components/ui";
import { CoursewarePlayer } from "./CoursewarePlayer";
import { draftPlaybackDocument } from "./coursewarePresentation";
import { useRef, useState } from "react";

/** Compose the teacher editor from the version-bound document workflow. */
export function CoursewareDocumentWorkspace({ coursewareId }: { coursewareId: string }) {
  const navigate = useNavigate();
  const [draftDirty, setDraftDirty] = useState(false);
  const [selectedPageId, setSelectedPageId] = useState<string>();
  const editorDetails = useRef<HTMLDetailsElement>(null);
  const { document, loading, busy, error, notice, reload, savePage, regenerate, review, publish } = useCoursewareDocumentWorkflow(coursewareId);
  const aiReview = useCoursewareAiReview(coursewareId, document?.recordVersion);
  const openPage = (pageId: string) => {
    if (draftDirty) return;
    setSelectedPageId(pageId);
    if (editorDetails.current) editorDetails.current.open = true;
  };
  if (loading) return <main className="courseware-route-state"><p role="status">正在读取教师课件草稿…</p></main>;
  if (!document) return <main className="courseware-route-state"><h1>课件草稿暂不可用</h1><p role="alert">{error || "找不到这份课件。"}</p><Button type="button" onClick={() => void reload()}>重新读取课件</Button><button type="button" onClick={() => navigate("/studio/courseware")}>返回教材课件</button></main>;
  return <div className="courseware-authoring-route">
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}
    <section className="courseware-final-confirmation"><div><h1>{document.title}</h1><p>{document.generationStatus === "completed" ? "教程已生成。先预览，需要时再修改；最后一次确认即可保存或发布。" : document.generationStatus === "failed" || document.generationStatus === "partial" ? "部分页面未能完成。打开问题页核对教材来源，修订或重做后再预览发布。" : "正在自动生成教程页面；完成后即可预览，不需要逐页批准。"}</p>
      {notice && <p role="status">{notice}</p>}
      {draftDirty && <p role="status">请先保存当前页面修改，再确认教程。</p>}
      </div><div className="courseware-final-actions">
      <Button variant="primary" disabled={busy || draftDirty || document.status === "published" || document.generationStatus !== "completed" || document.sourceReadiness !== "ready" || document.reviewIssues.length > 0 || !document.pages.length || document.pages.some((page) => page.generationStatus !== "completed" || Boolean(page.error))} onClick={() => void publish()}>{document.status === "published" ? "已发布" : document.sourceDistributionScope === "private" ? "确认并保存私有教程" : "确认并发布教程"}</Button>
      {document.publicationId && <ButtonLink to={`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`}>打开授课</ButtonLink>}
      {document.publicationId && document.sourceDistributionScope !== "private" && <ButtonLink to={`/learn/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`}>打开学生版</ButtonLink>}
      </div>
    </section>
    {(document.generationStatus === "failed" || document.generationStatus === "partial" || document.reviewIssues.length > 0 || document.sourceReadiness !== "ready") && <section className="courseware-job-status" aria-label="下一步操作">
      {document.sourceReadiness !== "ready" && <p>教材来源不可用或需复核；请先回到教材来源检查原页、修订和分发范围。</p>}
      <Button type="button" disabled={busy || draftDirty} onClick={() => openPage(document.pages.find((page) => page.generationStatus === "failed" || page.error)?.pageId ?? document.pages[0]?.pageId ?? "")}>打开问题页与恢复操作</Button>
      <ButtonLink to={`/studio/chapters/${encodeURIComponent(document.chapterId)}`}>检查教材原页</ButtonLink>
      <Button type="button" disabled={busy || draftDirty} onClick={() => void reload()}>重新读取制作状态</Button>
    </section>}
    {document.generationStatus === "completed" && document.pages.length > 0 && <section aria-label="教程最终预览"><CoursewarePlayer document={draftPlaybackDocument(document)} playbackScope={`teacher:draft:${coursewareId}`} /></section>}
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
      busy={busy}
      notice={notice}
      onSavePage={savePage}
      onRegeneratePage={regenerate}
      onReviewPage={(pageId, decision, note) => review(decision, [pageId], note)}
      onReviewDocument={review}
      onPublish={publish}
      onReload={reload}
      onPresent={() => document.publicationId && navigate(`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
      onLearn={() => document.publicationId && navigate(`/learn/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
      onDirtyChange={setDraftDirty}
      selectedPageId={selectedPageId}
      onSelectPage={setSelectedPageId}
    />
    </details>
    {document.publicationId && <CoursewareMediaPanel coursewareId={coursewareId} publicationId={document.publicationId} />}
  </div>;
}
