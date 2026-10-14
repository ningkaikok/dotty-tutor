import { useRef } from "react";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
import { Button, TextField, TextAreaField } from "../../components/ui";
import { Link } from "react-router";
import { CoursewareSourcePrepare } from "./CoursewareSourcePrepare";
import { CoursewarePlanEditor } from "./CoursewarePlanEditor";
import { useCoursewarePlanWorkflow } from "./useCoursewarePlanWorkflow";
import "./courseware.css";

/** Page composition only; API state and asynchronous workflows live in the dedicated hook. */
export function CoursewarePlanWorkspace() {
  const workflow = useCoursewarePlanWorkflow();
  const sourceRangeFields = useRef<HTMLFieldSetElement>(null);
  const {
    uploadId, chapterId, profiles, profilesLoaded, uploads, chapters, upload, chapter, plan, documents,
    job, jobPurpose, preparedChapters, loading, busy, error, notice, audience, setAudience,
    languagePolicy, setLanguagePolicy, durationMinutes, setDurationMinutes, objectives, setObjectives,
    pageStart, setPageStart, pageEnd, setPageEnd, startSourcePreparation, startPlan, savePlan, approvePlan,
    chooseUpload, chooseChapter, planLoaded, selectCompletionRange, supplementing, cancelSupplement, reloadWorkspace, automaticGeneration, retryAutomatic,
  } = workflow;

  if (loading || (chapterId && !profilesLoaded)) return <main className="courseware-route-state"><p role="status">正在读取教材来源与课件规划…</p></main>;
  if (uploadId && !upload) return <main className="courseware-route-state"><p role="alert">找不到所选上传文件；文件可能已删除或当前账号无权读取。</p><Link to="/studio">返回教材库</Link></main>;

  if (!uploadId && !chapterId) return <main className="app-shell courseware-workflow-shell">
    <header className="courseware-workflow-header"><Link to="/studio">← 我的教材</Link><span className="courseware-eyebrow">TEXTBOOK COURSEWARE</span><h1>制作教材互动课件</h1><p>选择学科与页码后自动制作教程，完成后一次预览确认；需要时可调整规划和页面。试卷继续使用原有练习流程。</p></header>
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}
    <section className="courseware-source-picker"><h2>从已上传教材开始</h2>{uploads.filter((item) => item.materialKind !== "paper").map((item) => <Button type="button" key={item.uploadId} onClick={() => chooseUpload(item.uploadId)}><strong>{item.filename}</strong><span>{item.materialKind === "textbook" ? "教材" : "待确认材料"} · {item.pageCount ?? "?"} 页</span></Button>)}{!uploads.some((item) => item.materialKind !== "paper") && <p>当前没有可用的教材来源。<Link to="/studio/import">上传 PDF 或图片</Link>后再继续。</p>}</section>
    <section className="courseware-source-picker"><h2>继续已有教材章节</h2>{chapters.filter((item) => item.teachingMode === "tutorial").map((item) => <Button type="button" key={item.chapterId} onClick={() => chooseChapter(item.chapterId)}><strong>{item.title}</strong><span>{profileLabel(profiles, item.subject)} · {item.status}</span></Button>)}{!chapters.some((item) => item.teachingMode === "tutorial") && <p>准备好教材来源后，章节会显示在这里。</p>}</section>
  </main>;

  if (upload) return <main className="app-shell courseware-workflow-shell">
    <header className="courseware-workflow-header"><Link to="/studio">← 我的教材</Link><span className="courseware-eyebrow">STEP 1 · SOURCE PREPARATION</span><h1>建立教材章节来源</h1><p>选好教材范围后默认自动制作，最后一次预览确认即可；原页识别失败时会提示具体问题。</p></header>
    {notice && <p role="status" className="courseware-authoring-notice">{notice}</p>}
    <CoursewareSourcePrepare upload={upload} profiles={profiles} busy={busy || Boolean(job && !isTerminal(job.status))} error={error} onStart={startSourcePreparation} />
    {job && jobPurpose === "source" && <section className="courseware-job-status" role="status"><strong>来源准备任务：{jobLabel(job.status)}</strong><span>{job.message}</span>{job.status === "queued" || job.status === "running" ? <span>{Math.max(0, Math.min(100, Math.round(job.progress)))}%</span> : null}</section>}
    {preparedChapters.length > 0 && <section className="courseware-prepared-chapters"><h2>可用教材章节</h2>{preparedChapters.map((item) => <article key={item.chapterId}><div><strong>{item.title}</strong><span>{profileLabel(profiles, item.subject)} · 教材第 {item.pageStart}–{item.pageEnd} 页</span></div><Button type="button" onClick={() => chooseChapter(item.chapterId)}>开始规划这份课件</Button></article>)}</section>}
  </main>;

  if (!chapter) return <main className="courseware-route-state"><p role="alert">{error || "教材章节不存在。"}</p><Link to="/studio/courseware">返回选择教材</Link></main>;
  const latestSource = chapter.sourceRevisions[chapter.sourceRevisions.length - 1];
  if (chapter.teachingMode !== "tutorial") return <main className="courseware-route-state"><h1>这不是教材教程章节</h1><p>试卷和练习章节保留在原有工作流。请从教材来源重新确认材料类型后制作互动课件。</p><Link to={`/studio/courseware?uploadId=${encodeURIComponent(latestSource?.uploadId ?? "")}`}>选择教材来源</Link></main>;
  if (!profiles.some((profile) => profile.subjectId === chapter.subject)) return <main className="courseware-route-state"><h1>学科尚未配置</h1><p>服务器未注册“{chapter.subject}”学科配置，因此不能生成课件。</p><Link to="/studio/courseware">返回教材列表</Link></main>;
  const sourceBounds = { start: latestSource?.pageStart ?? 1, end: latestSource?.pageEnd ?? 1 };
  const coveragePanel = <CoursewareCoveragePanel chapterId={chapterId} revisionKey={plan?.recordVersion} sourceRevisionId={latestSource?.sourceRevisionId} expectedRecordVersion={chapter.recordVersion} planStatus={plan?.status} busy={busy || !planLoaded} onSelectRange={planLoaded && (!plan || plan.status === "approved") ? (range) => { selectCompletionRange(range); sourceRangeFields.current?.querySelector("input")?.focus(); } : undefined} />;

  return <main className="app-shell courseware-workflow-shell">
    <header className="courseware-workflow-header"><Link to="/studio/courseware">← 教材课件</Link><span className="courseware-eyebrow">{automaticGeneration ? "STEP 2 · AUTOMATIC GENERATION" : plan ? "STEP 2 · PLAN REVIEW" : "STEP 2 · PLAN CREATION"}</span><h1>{chapter.title}</h1><p>{profileLabel(profiles, chapter.subject)} · 可用教材页码 {sourceBounds.start}–{sourceBounds.end} 页 · 来源修订 {chapter.sourceRevisions.length}</p>{latestSource?.sourceRevisionId && <details><summary>查看来源版本标识</summary><code>{latestSource.sourceRevisionId}</code></details>}</header>
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}{notice && <p role="status" className="courseware-authoring-notice">{notice}</p>}
    {automaticGeneration ? <details><summary>查看覆盖与补齐详情（可选）</summary>{coveragePanel}</details> : coveragePanel}
    {jobPurpose === "plan" && job && !isTerminal(job.status) && <section className="courseware-job-status" role="status"><strong>教学规划生成：{jobLabel(job.status)}</strong><span>{job.message}</span><span>{Math.max(0, Math.min(100, Math.round(job.progress)))}%</span></section>}
    {!planLoaded ? <section className="courseware-plan-create"><h2>暂时无法确认现有规划</h2><p>请先重新读取章节与规划，避免将已有教师工作覆盖为新规划。</p><Button onClick={() => void reloadWorkspace()}>重新读取规划</Button></section> : !plan || supplementing ? <section className="courseware-plan-create" id="courseware-plan-create"><span className="courseware-eyebrow">{supplementing ? "COMPLETE THE UNIT" : "SOURCE FIRST, THEN PLAN"}</span><h2>{supplementing ? "补齐其余教材原页" : "生成可审核的教学规划"}</h2><p>{supplementing ? "已确认的课时会保留，新规划只覆盖所选原页；同样要经教师确认后才生成页面。" : "规划生成会引用你明确选择的教材页码。课件页面不会在规划确认之前生成。"}</p><TextField label="学习对象" value={audience} maxLength={200} onChange={(event) => setAudience(event.target.value)} placeholder="描述班级或学习阶段" /><TextField label="授课语言策略" value={languagePolicy} maxLength={500} onChange={(event) => setLanguagePolicy(event.target.value)} placeholder="说明授课语言和术语处理方式" /><TextField label="课时总时长（分钟）" type="number" min="10" max="120" value={durationMinutes} onChange={(event) => setDurationMinutes(Number(event.target.value))} /><fieldset ref={sourceRangeFields} className="courseware-plan-source-range"><legend>本次规划教材页码（必须明确选择，最多 30 页）</legend><TextField label="起始页" type="number" min={sourceBounds.start} max={sourceBounds.end} value={pageStart} onChange={(event) => setPageStart(event.target.value)} /><TextField label="结束页" type="number" min={sourceBounds.start} max={sourceBounds.end} value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} /></fieldset><TextAreaField label="学习目标（每行一个）" rows={5} value={objectives} onChange={(event) => setObjectives(event.target.value)} placeholder="写下本节课希望学生完成的学习目标" /><Button type="button" className="is-primary" disabled={busy || !audience.trim() || !languagePolicy.trim() || !objectives.trim() || !pageStart || !pageEnd || durationMinutes < 10 || durationMinutes > 120} onClick={() => void startPlan()}>{busy ? "正在生成规划…" : "生成规划供教师确认"}</Button>{supplementing && <Button type="button" disabled={busy} onClick={cancelSupplement}>取消补齐</Button>}</section> : automaticGeneration ? <section className="courseware-plan-create"><h2>自动制作教程</h2>{job?.status === "failed" && <Button disabled={busy} onClick={() => void retryAutomatic()}>重试自动制作</Button>}<p>来源与教学大纲会自动校验并进入页面生成，不需要逐项确认。完成后打开教程预览，最后一次确认发布即可。</p><details><summary>查看或调整教学规划（可选）</summary><CoursewarePlanEditor plan={plan} sourceBounds={sourceBounds} busy={busy || plan.generationStatus === "queued" || plan.generationStatus === "running"} onSave={savePlan} onApprove={approvePlan} /></details></section> : <CoursewarePlanEditor plan={plan} sourceBounds={sourceBounds} busy={busy || plan.generationStatus === "queued" || plan.generationStatus === "running"} onSave={savePlan} onApprove={approvePlan} />}
    {documents.length > 0 && <section className="courseware-document-list"><h2>课时课件</h2><p>每个课时会自动生成教程；打开预览后一次确认，需要时再编辑。</p><ul>{documents.map((item) => <li key={item.coursewareId}><span><strong>{item.title}</strong><small>{item.pageCount} 页 · {item.generationStatus} · {item.status}</small></span><Link to={`/studio/courseware/${encodeURIComponent(item.coursewareId)}`}>{automaticGeneration ? "打开教程预览 →" : "打开课件审核 →"}</Link>{item.publicationId && <Link to={`/learn/courseware/${encodeURIComponent(item.coursewareId)}?publicationId=${encodeURIComponent(item.publicationId)}`}>学生发布版</Link>}</li>)}</ul></section>}
  </main>;
}

function isTerminal(status: string) { return status === "succeeded" || status === "failed" || status === "cancelled"; }
function jobLabel(status: string) { return status === "queued" ? "排队中" : status === "running" ? "处理中" : status === "succeeded" ? "已完成" : status === "cancelled" ? "已取消" : "失败"; }
function profileLabel(profiles: Array<{ subjectId: string; label: string }>, subjectId: string) { return profiles.find((item) => item.subjectId === subjectId)?.label ?? subjectId; }
