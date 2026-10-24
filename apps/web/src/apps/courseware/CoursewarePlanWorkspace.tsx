import { useRef } from "react";
import { Link } from "react-router";
import { Badge, Button, PageHeader, TextAreaField, TextField } from "../../components/ui";
import type { components } from "../../types/generated/api";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
import { CoursewarePlanEditor } from "./CoursewarePlanEditor";
import { CoursewareSourcePrepare, type WholeBookPreparationInput } from "./CoursewareSourcePrepare";
import { WholeBookProgressPanel } from "./WholeBookProgressPanel";
import { useCoursewarePlanWorkflow } from "./useCoursewarePlanWorkflow";
import { useWholeBookRun } from "./useWholeBookRun";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { coursewareListStatus, coursewareSubjectLabel } from "./coursewareWorkspaceView";
import "./courseware.css";

type CoursewareSummary = components["schemas"]["CoursewareDocumentSummary"];

/** Page composition only; API state and asynchronous workflows live in the dedicated hook. */
export function CoursewarePlanWorkspace() {
  const workflow = useCoursewarePlanWorkflow();
  const wholeBook = useWholeBookRun(workflow.uploadId);
  const sourceRangeFields = useRef<HTMLFieldSetElement>(null);
  const bookWorkspace = useRef<HTMLElement>(null);
  const startWholeBook = async (input: WholeBookPreparationInput) => {
    const started = await wholeBook.start(input);
    if (started) window.requestAnimationFrame(() => {
      bookWorkspace.current?.querySelector(".whole-book-progress")?.scrollIntoView({ block: "start" });
    });
    return started;
  };
  const {
    uploadId, chapterId, profiles, profilesLoading, profilesLoaded, profilesError, uploads, chapters, upload, uploadError, chaptersError,
    chapter, plan, documents, documentsLoaded, documentsError, uploadDocuments, uploadDocumentErrors,
    uploadDocumentsLoading, reloadUploadCourseware, reloadMaterialLists, job, jobPurpose,
    preparedChapters, loading, busy, error, notice, audience, setAudience, languagePolicy,
    setLanguagePolicy, durationMinutes, setDurationMinutes, objectives, setObjectives, pageStart,
    setPageStart, pageEnd, setPageEnd, startSourcePreparation, startPlan, savePlan, approvePlan,
    chooseUpload, chooseChapter, planLoaded, selectCompletionRange, supplementing, cancelSupplement,
    reloadWorkspace, automaticGeneration, retryAutomatic, retrySource, retryPlan,
  } = workflow;

  const latestSource = chapter?.sourceRevisions[chapter.sourceRevisions.length - 1];
  const backTo = chapterId
    ? latestSource?.uploadId
      ? `/studio/courseware?uploadId=${encodeURIComponent(latestSource.uploadId)}`
      : "/studio"
    : "/studio";
  const backLabel = chapterId ? "教材管理" : "我的教材";
  const header = <MaterialsHeader backTo={backTo} backLabel={backLabel} subtitle="教材课件" />;

  if (loading || profilesLoading || (chapterId && !chapter && !error && !profilesError)) return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="正在读取教材课件" description="正在读取教材来源、规划和已有课件。" />
    <p role="status">请稍候…</p>
  </main>;

  if (uploadId && !upload) return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="教材暂不可用" description="读取教材文件或当前账号权限时遇到问题。" />
    <p role="alert">{uploadError || error || "教材文件暂时无法读取。"}</p>
    <Button type="button" onClick={reloadMaterialLists}>重新读取教材</Button><Link to="/studio">返回教材库</Link>
  </main>;

  if (chapterId && profilesError && !chapter) return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="学科配置暂时无法读取" description="请重新读取，或返回教材库。" />
    <p role="alert">{profilesError}</p><Button type="button" onClick={reloadMaterialLists}>重新读取学科配置</Button><Link to="/studio">返回教材库</Link>
  </main>;

  if (!uploadId && !chapterId) return <main className="app-shell courseware-workflow-shell">
    {header}
    <PageHeader className="courseware-workflow-header" eyebrow="教材课件" title="制作教材互动课件" description="选择一本教材继续制作。课件完成后可直接授课；只有未解决的问题需要介入，手动制作时可编辑。试卷继续使用原有练习流程。" />
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}
    <section className="courseware-source-picker" aria-label="从教材开始">
      <h2>从已上传教材开始</h2>
      {uploads.filter((item) => item.materialKind !== "paper").map((item) => <Button type="button" key={item.uploadId} onClick={() => chooseUpload(item.uploadId)}>
        <strong>{item.filename}</strong><span>{item.materialKind === "textbook" ? "教材" : "待确认材料"} · {item.pageCount ?? "页数待确认"} 页</span>
      </Button>)}
      {!uploads.some((item) => item.materialKind !== "paper") && <p>当前没有可用的教材来源。<Link to="/studio/import">上传 PDF 或图片</Link>后再继续。</p>}
    </section>
    <section className="courseware-source-picker" aria-label="继续已有章节">
      <h2>继续已有教材章节</h2>
      {chaptersError && <p role="alert">教材章节暂时无法读取：{chaptersError}<Button type="button" onClick={reloadMaterialLists}>重新读取</Button></p>}
      {chapters.filter((item) => item.teachingMode === "tutorial").map((item) => <Button type="button" key={item.chapterId} onClick={() => chooseChapter(item.chapterId)}>
        <strong>{item.title}</strong><span>{coursewareSubjectLabel(item.subject, profiles.find((profile) => profile.subjectId === item.subject)?.label)} · 从教材第 {item.pageStart ?? "?"} 页开始</span>
      </Button>)}
      {!chaptersError && !chapters.some((item) => item.teachingMode === "tutorial") && <p>准备好教材来源后，章节会显示在这里。</p>}
    </section>
  </main>;

  if (upload) {
    const relatedChapters = chapters.filter((item) => item.uploadId === uploadId && item.teachingMode === "tutorial");
    const wholeBookRun = wholeBook.run?.uploadId === uploadId ? wholeBook.run : null;
    const relatedSubjects = new Set(relatedChapters.map((item) => item.subject));
    const [uniqueSubject] = relatedSubjects;
    const initialSubject = relatedSubjects.size === 1 && profiles.some((profile) => profile.subjectId === uniqueSubject) ? uniqueSubject : null;
    const hasExistingCourseware = relatedChapters.some((item) => (uploadDocuments[item.chapterId] ?? []).length > 0);
    const collapseSourcePreparation = hasExistingCourseware || Boolean(wholeBookRun);
    const sourcePreparation = <CoursewareSourcePrepare key={upload.uploadId} upload={upload} profiles={profiles} initialSubject={initialSubject} busy={busy || wholeBook.busy || Boolean(job && !isTerminal(job.status))} error={error} onStart={startSourcePreparation} onStartWholeBook={startWholeBook} />;
    const wholeBookProgress = <WholeBookProgressPanel run={wholeBookRun} loading={wholeBook.loading} busy={wholeBook.busy} error={wholeBook.error} onPause={wholeBook.pause} onResume={wholeBook.resume} onRefresh={wholeBook.refresh} />;

    return <main ref={bookWorkspace} className="app-shell courseware-workflow-shell">
      <MaterialsHeader backTo="/studio" backLabel="我的教材" subtitle="教材管理" actions={<Button type="button" onClick={reloadMaterialLists}>重新读取</Button>} />
      <PageHeader className="courseware-workflow-header" eyebrow="教材管理" title={upload.filename} description={`${upload.pageCount ?? "页数暂不可用"} 页 · 查看已有关联章节和课件，也可明确开始整本或指定页段制作。`} />
      {uploadError && <p role="alert" className="courseware-editor-error">{uploadError}</p>}
      {chaptersError && <p role="alert" className="courseware-editor-error">教材章节暂时无法读取：{chaptersError}</p>}
      {wholeBookProgress}
      <section className="courseware-document-list" aria-label="教材章节与课件">
        <h2>教材章节与课件</h2>
        {uploadDocumentsLoading && <p role="status">正在读取关联课件…</p>}
        {uploadDocumentErrors.length > 0 && <div role="alert"><p>部分课件暂时无法显示。请重新读取，或返回教材库。</p><Button type="button" disabled={uploadDocumentsLoading} onClick={() => void reloadUploadCourseware()}>重新读取课件</Button></div>}
        {relatedChapters.map((item) => {
          const items = uploadDocuments[item.chapterId] ?? [];
          const failed = uploadDocumentErrors.includes(item.chapterId);
          return <article key={item.chapterId}>
            <h3>{item.title}</h3>
            <p>{coursewareSubjectLabel(item.subject, profiles.find((profile) => profile.subjectId === item.subject)?.label)} · 教材第 {item.pageStart ?? "?"} 页起</p>
            {failed ? <p role="alert">这个章节的课件暂时无法显示。请重新读取，或返回教材库。</p> : <CoursewareDocumentLinks documents={items} loading={uploadDocumentsLoading} />}
            <Link to={`/studio/courseware?chapterId=${encodeURIComponent(item.chapterId)}`} aria-label={`管理章节课件 · ${item.title}`}>管理章节课件 →</Link>
          </article>;
        })}
        {!chaptersError && !uploadDocumentsLoading && !uploadDocumentErrors.length && !relatedChapters.length && <p>这本教材还没有关联章节。开始新制作后，系统会按所选范围建立章节来源。</p>}
      </section>
      {notice && <p role="status" className="courseware-authoring-notice">{notice}</p>}
      {profilesError ? <div role="alert"><p>学科配置暂时无法读取。请重新读取，或返回教材库。</p><Button type="button" onClick={reloadMaterialLists}>重新读取学科配置</Button></div>
        : chaptersError || uploadDocumentErrors.length > 0 || uploadDocumentsLoading
          ? <p role="status">请重新读取教材章节和课件，或返回教材库。</p>
          : collapseSourcePreparation
            ? <details className="courseware-whole-book-settings"><summary>开始新制作（整本或指定页段）</summary><p>选择范围后开始，完成后可直接授课。</p>{sourcePreparation}</details>
            : <section aria-label="开始新制作">{upload.pageCount !== undefined && upload.pageCount > 30 && upload.pageCount <= 1000 && <p>可整本自动续做，也可选择指定页段。选择范围后开始，完成后可直接授课。</p>}{sourcePreparation}</section>}
      {job && jobPurpose === "source" && <section className="courseware-job-status" role="status"><strong>教材来源准备：{jobLabel(job.status)}</strong><span>{job.message}</span>{job.status === "queued" || job.status === "running" ? <span>{Math.max(0, Math.min(100, Math.round(job.progress)))}%</span> : null}</section>}
      {jobPurpose === "source" && job?.status === "failed" && <section className="courseware-job-status" aria-label="来源准备恢复"><h2>教材来源准备未完成</h2><p>{typeof job.lastError?.message === "string" ? job.lastError.message : "请核对上方失败原页与教材文件。"}</p><p>可重试原任务并保留已识别原页；若教材文件或所选范围有误，再调整设置。</p><Button type="button" disabled={busy} onClick={() => void retrySource()}>重试原来源任务</Button></section>}
      {preparedChapters.length > 0 && <section className="courseware-prepared-chapters"><h2>已准备的教材章节</h2>{preparedChapters.map((item) => <article key={item.chapterId}><div><strong>{item.title}</strong><span>{coursewareSubjectLabel(item.subject)} · 教材第 {item.pageStart}–{item.pageEnd} 页</span></div><Button type="button" onClick={() => chooseChapter(item.chapterId)}>继续制作课件</Button></article>)}</section>}
    </main>;
  }

  if (!chapter) return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="教材章节暂不可用" description="请重新读取，或返回教材库。" />
    <p role="alert">{error || "教材章节不存在或当前账号无权读取。"}</p><Button type="button" onClick={() => void reloadWorkspace()}>重新读取章节</Button>
    {latestSource?.uploadId && <Link to={`/studio/courseware?uploadId=${encodeURIComponent(latestSource.uploadId)}`}>返回教材库</Link>}
  </main>;

  if (chapter.teachingMode !== "tutorial") return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="这不是教材教程章节" description="试卷和练习章节保留在原有工作流。" />
    <p>请从教材来源重新确认材料类型后制作互动课件。</p><Link to={`/studio/courseware?uploadId=${encodeURIComponent(latestSource?.uploadId ?? "")}`}>返回教材来源</Link>
  </main>;

  if (profilesLoaded && !profilesError && !profiles.some((profile) => profile.subjectId === chapter.subject)) return <main className="courseware-route-state app-shell">
    {header}<PageHeader className="courseware-workflow-header" title="学科尚未配置" description="服务器未注册该学科配置，因此不能生成课件。" />
    <p>请联系管理员配置当前学科后再继续。</p><Link to="/studio/courseware">返回教材列表</Link>
  </main>;

  const sourceBounds = { start: latestSource?.pageStart ?? 1, end: latestSource?.pageEnd ?? 1 };
  const hasPublishedCourseware = documents.some((item) => item.status === "published" && item.publicationId);
  const activePlanJob = jobPurpose === "plan" && job && !isTerminal(job.status);
  const coveragePanel = <CoursewareCoveragePanel chapterId={chapterId} revisionKey={plan?.recordVersion} sourceRevisionId={latestSource?.sourceRevisionId} expectedRecordVersion={chapter.recordVersion} planStatus={plan?.status} busy={busy || !planLoaded} onSelectRange={planLoaded && (!plan || plan.status === "approved") ? (range) => { selectCompletionRange(range); sourceRangeFields.current?.querySelector("input")?.focus(); } : undefined} />;

  const planningWorkflow = <>
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}{notice && <p role="status" className="courseware-authoring-notice">{notice}</p>}
    {(hasPublishedCourseware || automaticGeneration) ? <details><summary>查看教材覆盖与补齐详情（可选）</summary>{coveragePanel}</details> : coveragePanel}
    {activePlanJob && <section className="courseware-job-status" role="status"><strong>教学规划：{jobLabel(job.status)}</strong><span>{job.message}</span><span>{Math.max(0, Math.min(100, Math.round(job.progress)))}%</span></section>}
    {jobPurpose === "plan" && job && (job.status === "failed" || job.status === "cancelled") && <section className="courseware-job-status" aria-label="规划恢复"><h2>教学规划{jobLabel(job.status)}</h2><p>{typeof job.lastError?.message === "string" ? job.lastError.message : job.message}</p><p>重新读取后可确认已有规划与课时；失败任务可沿用原设置重试。</p><Button type="button" disabled={busy} onClick={() => void reloadWorkspace()}>重新读取规划与课件</Button>{job.status === "failed" && plan?.status !== "approved" && <Button type="button" disabled={busy} onClick={() => void retryPlan()}>重试原规划任务</Button>}{latestSource?.uploadId && <Link to={`/studio/chapters/${encodeURIComponent(chapterId)}`}>检查教材原页</Link>}</section>}
    {!planLoaded ? <section className="courseware-plan-create"><h2>暂时无法确认现有规划</h2><p>请先重新读取章节与规划，避免将已有教师工作覆盖为新规划。</p><Button onClick={() => void reloadWorkspace()}>重新读取规划</Button></section> : !plan || supplementing ? <section className="courseware-plan-create" id="courseware-plan-create">
      <h2>{supplementing ? "补齐其余教材原页" : "制作新课件"}</h2><p>{supplementing ? "已有课件会保留；系统只制作所选原页，并自动检查和修复。" : "选择本次教材页码，系统可自动完成规划、生成、检查和保存。手动模式仍可编辑规划与页面。"}</p>
      <fieldset ref={sourceRangeFields} className="courseware-plan-source-range"><legend>本次制作教材页码（必须明确选择，最多 30 页）</legend><TextField label="起始页" type="number" min={sourceBounds.start} max={sourceBounds.end} value={pageStart} onChange={(event) => setPageStart(event.target.value)} /><TextField label="结束页" type="number" min={sourceBounds.start} max={sourceBounds.end} value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} /></fieldset>
      <details><summary>调整教学要求（可选）</summary><TextField label="学习对象" value={audience} maxLength={200} onChange={(event) => setAudience(event.target.value)} placeholder="描述班级或学习阶段" /><TextField label="授课语言策略" value={languagePolicy} maxLength={500} onChange={(event) => setLanguagePolicy(event.target.value)} placeholder="说明授课语言和术语处理方式" /><TextField label="课时总时长（分钟）" type="number" min="10" max="120" value={durationMinutes} onChange={(event) => setDurationMinutes(Number(event.target.value))} /><TextAreaField label="学习目标（每行一个）" rows={5} value={objectives} onChange={(event) => setObjectives(event.target.value)} placeholder="写下本节课希望学生完成的学习目标" /><Button type="button" disabled={busy || !audience.trim() || !languagePolicy.trim() || !objectives.trim() || !pageStart || !pageEnd} onClick={() => void startPlan(false)}>手动生成规划并编辑</Button></details>
      <Button type="button" className="is-primary" disabled={busy || !audience.trim() || !languagePolicy.trim() || !objectives.trim() || !pageStart || !pageEnd || durationMinutes < 10 || durationMinutes > 120} onClick={() => void startPlan()}>{busy ? "正在制作课件…" : "自动制作本次课件"}</Button>{supplementing && <Button type="button" disabled={busy} onClick={cancelSupplement}>取消补齐</Button>}
    </section> : automaticGeneration ? <section className="courseware-plan-create"><h2>自动制作状态</h2>{job?.status === "cancelled" && <Button disabled={busy} onClick={() => void retryAutomatic()}>继续自动制作</Button>}<p>来源、规划与页面会由系统自动检查和修复；完成后可直接预览和使用。</p><details><summary>查看或调整教学规划（可选）</summary><CoursewarePlanEditor plan={plan} sourceBounds={sourceBounds} busy={busy || plan.generationStatus === "queued" || plan.generationStatus === "running"} onSave={savePlan} onApprove={approvePlan} /></details></section> : <CoursewarePlanEditor plan={plan} sourceBounds={sourceBounds} busy={busy || plan.generationStatus === "queued" || plan.generationStatus === "running"} onSave={savePlan} onApprove={approvePlan} />}
  </>;

  const documentList = <section className="courseware-document-list" aria-label="章节课件">
    <h2>已有课件</h2>
    {!documentsLoaded && !documentsError && <p role="status">正在读取课件列表…</p>}
    {documentsError && <div role="alert"><p>课件暂时无法显示，请重新读取，或返回教材库。</p><Button type="button" disabled={busy} onClick={() => void reloadWorkspace()}>重新读取课件</Button></div>}
    {documentsLoaded && !documentsError && <CoursewareDocumentLinks documents={documents} />}
    {documentsLoaded && !documentsError && documents.length === 0 && <p>目前没有课件。选择“制作新课件”后可使用已准备的教材来源。</p>}
  </section>;

  return <main className="app-shell courseware-workflow-shell">
    <MaterialsHeader backTo={latestSource?.uploadId ? `/studio/courseware?uploadId=${encodeURIComponent(latestSource.uploadId)}` : "/studio"} backLabel={latestSource?.uploadId ? "教材管理" : "我的教材"} subtitle="章节课件" />
    <PageHeader className="courseware-workflow-header" eyebrow="教材章节" title={chapter.title} description={`${coursewareSubjectLabel(chapter.subject, profiles.find((profile) => profile.subjectId === chapter.subject)?.label)} · 教材第 ${sourceBounds.start}–${sourceBounds.end} 页`} />
    {profilesError && <div role="alert"><p>学科配置暂时无法读取。现有课件仍可查看；请重新读取，或返回教材库。</p><Button type="button" onClick={reloadMaterialLists}>重新读取学科配置</Button></div>}
    {documentList}
    {activePlanJob && planningWorkflow}
    {!profilesError && !activePlanJob && documentsLoaded && !documentsError && hasPublishedCourseware && <details className="courseware-whole-book-settings"><summary>继续制作新课件或补齐教材页</summary>{planningWorkflow}</details>}
    {!profilesError && !activePlanJob && documentsLoaded && !documentsError && !hasPublishedCourseware && planningWorkflow}
  </main>;
}

function CoursewareDocumentLinks({ documents, loading = false }: { documents: CoursewareSummary[]; loading?: boolean }) {
  if (loading && !documents.length) return null;
  if (!documents.length) return <p>这个章节还没有课件。</p>;
  return <ul>{documents.map((item) => {
    const status = coursewareListStatus(item);
    const tone = status.kind === "published" ? "success" : status.kind === "needs-attention" ? "warning" : status.kind === "making" ? "info" : "neutral";
    return <li key={item.coursewareId}>
      <span><strong>{item.title}</strong> · {item.pageCount} 页 · <Badge tone={tone}>{status.label}</Badge></span>
      <Link to={`/studio/courseware/${encodeURIComponent(item.coursewareId)}`}>{item.status === "published" ? "查看课件" : "查看课件与进度"}</Link>
      {item.status === "published" && item.publicationId && <Link to={`/teacher/courseware/${encodeURIComponent(item.coursewareId)}?publicationId=${encodeURIComponent(item.publicationId)}`}>直接授课</Link>}
    </li>;
  })}</ul>;
}

function isTerminal(status: string) { return status === "succeeded" || status === "failed" || status === "cancelled"; }
function jobLabel(status: string) { return status === "queued" ? "排队中" : status === "running" ? "处理中" : status === "succeeded" ? "已完成" : status === "cancelled" ? "已取消" : "失败"; }
