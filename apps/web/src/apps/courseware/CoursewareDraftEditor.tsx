import { useEffect, useState } from "react";
import { CoursewarePageEditor } from "./CoursewarePageEditor";
import type { CoursewareAuthoringDocument, CoursewarePageEditView } from "./coursewareAuthoringView";

const statusLabel: Record<CoursewareAuthoringDocument["status"], string> = {
  draft: "草稿",
  in_review: "审核中",
  ready: "审核通过",
  published: "已发布",
  deleted: "已删除",
};

const distributionLabel: Record<"private" | "classroom" | "public", string> = {
  private: "仅教师可见",
  classroom: "课堂分发",
  public: "公开分发",
};

function subjectLabel(subject: string) {
  if (subject === "english") return "英语";
  if (subject === "math") return "数学";
  return subject.trim() || "未配置学科";
}

export function CoursewareDraftEditor({
  document,
  busy = false,
  notice = "",
  onSavePage,
  onRegeneratePage,
  onReviewPage,
  onReviewDocument,
  onPublish,
  onReload,
  onPresent,
  onLearn,
}: {
  document: CoursewareAuthoringDocument;
  busy?: boolean;
  notice?: string;
  onSavePage: (pageId: string, value: CoursewarePageEditView) => Promise<void>;
  onRegeneratePage: (pageId: string, reason: string) => Promise<void>;
  onReviewPage: (pageId: string, decision: "approve" | "request_changes", note: string) => Promise<void>;
  onReviewDocument: (decision: "approve" | "request_changes", pageIds: string[], note: string) => Promise<void>;
  onPublish: () => Promise<void>;
  onReload: () => Promise<void>;
  onPresent: () => void;
  onLearn: () => void;
}) {
  const [pageId, setPageId] = useState(document.pages[0]?.pageId ?? "");
  const [selectedPages, setSelectedPages] = useState<string[]>([]);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [actionError, setActionError] = useState("");
  const page = document.pages.find((item) => item.pageId === pageId) ?? document.pages[0];
  const allApproved = document.pages.length > 0 && document.pages.every((item) => item.reviewStatus === "approved");
  const hasReviewIssues = document.reviewIssues.length > 0;
  const reviewablePageIds = new Set(document.pages.filter((item) => item.generationStatus === "completed" && item.blocks.length > 0 && !item.error).map((item) => item.pageId));
  const running = document.generationStatus === "queued" || document.generationStatus === "running";

  useEffect(() => {
    if (!document.pages.some((item) => item.pageId === pageId)) setPageId(document.pages[0]?.pageId ?? "");
    setSelectedPages((items) => items.filter((id) => document.pages.some((item) => item.pageId === id)));
  }, [document.pages, pageId]);

  const togglePage = (id: string) => setSelectedPages((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const reviewSelected = async (decision: "approve" | "request_changes") => {
    const pageIds = selectedPages.length ? selectedPages : page ? [page.pageId] : [];
    if (decision === "request_changes" && !reviewNote.trim()) return;
    if (!pageIds.length) return;
    setActionError("");
    try {
      await onReviewDocument(decision, pageIds, reviewNote.trim());
      setReviewNote("");
      setReviewOpen(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "审核意见未能保存。");
    }
  };
  const publish = async () => {
    setActionError("");
    try { await onPublish(); } catch (error) { setActionError(error instanceof Error ? error.message : "课件发布失败。"); }
  };

  return <main className="courseware-authoring-shell">
    <header className="courseware-authoring-header">
      <div><span className="courseware-eyebrow">教师课件工作台 · 第 {document.planVersion} 版规划</span><h1>{document.title}</h1><p>{subjectLabel(document.subject)} · {document.durationMinutes} 分钟 · {document.pages.length} 页</p></div>
      <div className="courseware-authoring-header-actions"><span className={`courseware-status-pill is-${document.status}`}>{statusLabel[document.status]}</span><button type="button" onClick={() => void onReload()} disabled={busy}>刷新</button><button type="button" onClick={onPresent} disabled={!document.publicationId}>教师授课预览</button>{document.publicationId && <button type="button" onClick={onLearn}>学生发布版</button>}</div>
    </header>

    <ol className="courseware-authoring-steps" aria-label="课件制作进度">
      <li aria-current={document.generationStatus === "completed" ? undefined : "step"}>1 逐页生成</li>
      <li aria-current={document.status === "in_review" || document.status === "draft" && document.generationStatus === "completed" ? "step" : undefined}>2 教师审核</li>
      <li aria-current={document.status === "ready" || document.status === "published" ? "step" : undefined}>3 发布授课</li>
    </ol>

    {notice && <p className="courseware-authoring-notice" role="status">{notice}</p>}
    {actionError && <p className="courseware-editor-error" role="alert">{actionError}</p>}
    {running && <section className="courseware-job-status" role="status"><span>{document.generationStatus === "queued" ? "课件生成排队中" : "正在逐页生成课件"}</span>{document.generationJobId && <span>任务 {document.generationJobId}</span>}<button type="button" onClick={() => void onReload()} disabled={busy}>检查进度</button></section>}
    {document.generationStatus === "partial" && <p className="courseware-editor-warning" role="status">部分页面尚未完成。已完成页面可以继续编辑；请先重做失败页面，再审核发布。</p>}
    {document.sourceReadiness !== "ready" && <p className="courseware-editor-warning" role="alert">教材来源尚未就绪，先处理来源页/图像问题后再审核。</p>}

    <section className="courseware-source-policy" aria-label="来源与许可">
      <h2>来源与许可</h2>
      <div>{document.sourceTitle && <strong>{document.sourceTitle}</strong>}{document.sourceUrl && <a href={document.sourceUrl} target="_blank" rel="noreferrer">查看教材来源</a>}{document.attributionText && <span>{document.attributionText}</span>}{document.sourceLicense && <span>许可：{document.sourceLicense}</span>}<span>分发范围：{distributionLabel[document.sourceDistributionScope]}</span></div>
      {document.assets.length > 0 && <ul>{document.assets.map((asset) => <li key={asset.assetId}>{asset.alt || asset.assetId} · {asset.license ? `许可：${asset.license}` : "未注明许可"} · {asset.distributionScope ? distributionLabel[asset.distributionScope] : "分发范围待确认"}{asset.attributionText ? ` · ${asset.attributionText}` : ""}</li>)}</ul>}
      {!document.sourceTitle && !document.attributionText && !document.sourceLicense && document.assets.every((asset) => !asset.license) && <p>来源署名和分发范围由服务端审核结果确认；存在待处理问题时不能发布。</p>}
    </section>

    <div className="courseware-authoring-layout">
      <aside className="courseware-page-list" aria-label="页面目录">
        <div className="courseware-page-list-heading"><h2>Deck</h2><span>{document.pages.length} pages</span></div>
        {document.sections.map((section) => <section key={section.sectionId} className="courseware-page-list-section"><h3>{section.title}<small>{section.estimatedMinutes} min</small></h3>{(section.pageIds ?? []).map((id) => {
          const item = document.pages.find((candidate) => candidate.pageId === id);
          if (!item) return null;
          return <div className="courseware-page-list-item" key={item.pageId}><button type="button" aria-current={page?.pageId === item.pageId ? "page" : undefined} onClick={() => setPageId(item.pageId)}><span>{item.title}</span><small>{item.generationStatus === "completed" ? "已生成" : item.generationStatus}</small><small>{item.reviewStatus === "approved" ? "已审" : item.reviewStatus === "changes_requested" ? "需修改" : "待审"}</small></button><label><input type="checkbox" aria-label={`选择审核 ${item.title}`} checked={selectedPages.includes(item.pageId)} onChange={() => togglePage(item.pageId)} />选审</label></div>;
        })}</section>)}
      </aside>

      {page ? <CoursewarePageEditor key={`${page.pageId}:${page.version}`} page={page} busy={busy || running} onSave={onSavePage} onRegenerate={onRegeneratePage} onReview={onReviewPage} /> : <section className="courseware-page-editor"><h2>正在等待页面生成</h2><p>任务完成后，这里会显示每一页及其来源依据。</p></section>}
    </div>

    <section className="courseware-document-review"><div><h2>教师审核与发布</h2><p>{allApproved && !hasReviewIssues ? "所有页面均已通过审核，且没有待处理的来源或分发问题。" : `已审核 ${document.pages.filter((item) => item.reviewStatus === "approved").length} / ${document.pages.length} 页。空白或未完成页面不能审核。`}</p></div><div className="courseware-review-issues">{document.reviewIssues.map((issue, index) => <p key={`${issue.code}-${issue.pageId ?? "doc"}-${index}`} role="alert">{issue.message}</p>)}{hasReviewIssues && <p role="status">请先处理上方的来源、内容或发布范围问题，再发布。</p>}</div><div className="courseware-document-actions"><button type="button" disabled={busy || running || document.sourceReadiness !== "ready" || !selectedPages.length || selectedPages.some((id) => !reviewablePageIds.has(id))} onClick={() => void reviewSelected("approve")}>审核所选页</button><button type="button" disabled={busy || running || !selectedPages.length || selectedPages.some((id) => !reviewablePageIds.has(id))} onClick={() => setReviewOpen((open) => !open)}>请求修改</button><button type="button" className="is-primary" disabled={busy || running || document.status !== "ready" || !allApproved || hasReviewIssues || document.sourceReadiness !== "ready"} onClick={() => void publish()}>{busy ? "发布中…" : document.status === "published" ? "已发布" : "发布课件"}</button></div>{reviewOpen && <div className="courseware-editor-command"><label>修改意见<textarea rows={2} maxLength={2000} value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="说明需要修改的页面和原因" /></label><button type="button" disabled={busy || !reviewNote.trim()} onClick={() => void reviewSelected("request_changes")}>提交审核意见</button><button type="button" onClick={() => setReviewOpen(false)}>取消</button></div>}</section>
  </main>;
}
