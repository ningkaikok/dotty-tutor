import { Badge, Button, Card, Notice, Skeleton } from "../../components/ui";
import { completionState, sourcePageLabel, type CompletionRange } from "./coursewareCompletionView";
import { useCoursewareCoverage } from "./useCoursewareCoverage";

interface CoveragePanelProps {
  chapterId: string;
  revisionKey?: number;
  sourceRevisionId?: string;
  expectedRecordVersion?: number;
  planStatus?: string;
  busy?: boolean;
  onSelectRange?: (range: CompletionRange) => void;
}
/** Whole-unit evidence and bounded suggestions share one surface; selecting only fills the existing planning form. */
export function CoursewareCoveragePanel({ chapterId, revisionKey, sourceRevisionId, expectedRecordVersion, planStatus, busy = false, onSelectRange }: CoveragePanelProps) {
  const { coverage: report, completion, coverageError, completionError, refresh } = useCoursewareCoverage(chapterId, revisionKey, sourceRevisionId);
  const state = completion && completionState(completion, { chapterId, sourceRevisionId, expectedRecordVersion, planStatus });
  const gaps = report && [
    ["缺失来源", report.missingSourcePages], ["未规划", report.unplannedPages],
    ["未生成", report.ungeneratedPages], ["未复核", report.unreviewedPages], ["未发布", report.unpublishedPages],
  ] as const;
  return <Card className="courseware-coverage" aria-label="完整单元覆盖">
    {coverageError ? <Notice tone="danger">完整单元覆盖暂时无法读取：{coverageError} <Button onClick={refresh}>重试覆盖报告</Button></Notice> : !report ? <Skeleton label="正在核对完整单元覆盖…" rows={1} /> : <>
      <div className="ui-actions"><h2>完整单元覆盖</h2><Badge tone={report.complete ? "success" : "warning"}>{report.complete ? "结构覆盖完整" : "仍有缺口"}</Badge></div>
      <p>教材原页 {report.pageStart}–{report.pageEnd}。页段通过不等于完整单元通过；教学质量仍需教师验收。</p>
      <dl>{gaps?.map(([label, pages]) => <div key={label}><dt>{label}</dt><dd>{pages.length ? `原页 ${sourcePageLabel(pages)}` : "无缺口"}</dd></div>)}</dl>
    </>}
    <section className="courseware-completion" aria-labelledby={`completion-${chapterId}`}>
      <h3 id={`completion-${chapterId}`}>补齐完整单元</h3>
      {completionError ? <Notice tone="danger">补齐建议暂时无法读取：{completionError} <Button onClick={refresh}>重试补齐建议</Button></Notice> : !completion ? <Skeleton label="正在整理补齐建议…" rows={1} /> : <>
        {state?.sourceChanged ? <Notice tone="warning" role="alert">来源版本已变化，请刷新教材章节后重新读取建议；当前页段不能用于规划。</Notice> : state?.versionChanged ? <Notice tone="warning" role="alert">章节版本已变化，请刷新页面核对现有规划，避免覆盖其他修改。</Notice> : <>
          <p>按连续教材原页分批，每批最多 {completion.maxPagesPerBatch} 页；已生成和已有规划覆盖的原页不会再列入建议。</p>
          {state?.hasPlan && <Notice tone="info">{state.planStale ? "现有规划引用旧来源，请先核对来源与规划版本。" : state.planStatus === "approved" ? "已确认规划请从下方课时课件继续生成与复核。" : "已有规划请在下方编辑器核对、保存并确认。"} 补齐建议不会覆盖现有规划；扩展单元须遵循原规划生命周期。</Notice>}
          {completion.action === "wait_generation" && <Notice tone="info">现有生成任务仍在处理中，请等待任务结束再核对补齐建议。</Notice>}
          {Boolean(completion.alreadyPlannedPages?.length) && <p>已纳入规划：原页 {sourcePageLabel(completion.alreadyPlannedPages ?? [])}。</p>}
          {Boolean(completion.alreadyGeneratedPages?.length) && <p>已生成：原页 {sourcePageLabel(completion.alreadyGeneratedPages ?? [])}。</p>}
          {Boolean(completion.pendingGenerationPages?.length) && <p>已有规划待生成：原页 {sourcePageLabel(completion.pendingGenerationPages ?? [])}；请继续原规划任务，失败页面可在课件审核中重试。</p>}
          {Boolean(completion.pendingReviewPages?.length) && <p>待教师复核：原页 {sourcePageLabel(completion.pendingReviewPages ?? [])}；补齐来源不能替代逐页复核。</p>}
          {Boolean(completion.pendingPublicationPages?.length) && <p>尚未发布：原页 {sourcePageLabel(completion.pendingPublicationPages ?? [])}；请完成审核后从原课件工作台发布。</p>}
          {Boolean(completion.blockedPages?.length) && <Notice tone="warning">原页 {sourcePageLabel(completion.blockedPages ?? [])} 的来源缺失、为空或带阻断标记，请先修复来源再规划。</Notice>}
          {completion.batches?.length ? <ol className="courseware-completion-batches" aria-label="连续缺口补齐页段">{completion.batches.map((batch) => <li key={`${batch.pageStart}-${batch.pageEnd}`}><span>教材原页 {sourcePageLabel(batch.sourcePages)}（{batch.sourcePages.length} 页）</span>{state?.canSelect && onSelectRange && <Button disabled={busy} onClick={() => onSelectRange({ pageStart: batch.pageStart, pageEnd: batch.pageEnd })}>规划教材原页 {sourcePageLabel(batch.sourcePages)}</Button>}</li>)}</ol> : <p>{completion.action === "complete" ? "来源范围内没有待补齐页段。" : "当前没有可新建规划的连续页段，请先处理来源或继续现有任务。"}</p>}
          {state?.canSelect && onSelectRange && <p>选择页段只填写下方规划范围；仍需填写学习目标并由教师确认，不会自动生成页面、审核或发布。</p>}
        </>}
      </>}
    </section>
  </Card>;
}
