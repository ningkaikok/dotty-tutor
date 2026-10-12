import { useState } from "react";
import { editablePlan, type CoursewarePlanEditView, type CoursewarePlanPageKind, type CoursewarePlanView } from "./coursewarePlanView";

const pageKinds: CoursewarePlanPageKind[] = ["cover", "explanation", "vocabulary", "reading", "worked_example", "activity", "practice", "summary"];
const layouts: CoursewarePlanView["sessions"][number]["sections"][number]["pages"][number]["layout"][] = ["hero_split", "two_column", "cards", "text_focus", "figure_focus"];

export function CoursewarePlanEditor({ plan, sourceBounds, busy, onSave, onApprove }: {
  plan: CoursewarePlanView;
  sourceBounds: { start: number; end: number };
  busy: boolean;
  onSave: (value: CoursewarePlanEditView) => Promise<void>;
  onApprove: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<CoursewarePlanEditView>(() => editablePlan(plan));
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");

  const update = (change: (previous: CoursewarePlanEditView) => CoursewarePlanEditView) => {
    setDraft((previous) => change(previous));
    setDirty(true);
  };
  const updatePage = (pageId: string, change: (page: CoursewarePlanEditView["sessions"][number]["sections"][number]["pages"][number]) => CoursewarePlanEditView["sessions"][number]["sections"][number]["pages"][number]) => update((previous) => ({
    ...previous,
    sessions: previous.sessions.map((session) => ({ ...session, sections: session.sections.map((section) => ({
      ...section,
      pages: section.pages.map((page) => page.pageId === pageId ? change(page) : page),
    })) })),
  }));
  const save = async () => {
    if (!validRange(draft.pageStart, draft.pageEnd, sourceBounds)) {
      setActionError(`规划范围必须位于教材第 ${sourceBounds.start}–${sourceBounds.end} 页内，且不超过 80 页。`);
      return;
    }
    setActionError("");
    try {
      await onSave(draft);
      setDirty(false);
      setNotice("规划已保存，可继续核对页面顺序与教材依据。");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "规划保存失败，请刷新后重试。");
    }
  };
  const approve = async () => {
    if (!validRange(draft.pageStart, draft.pageEnd, sourceBounds)) {
      setActionError(`规划范围必须位于教材第 ${sourceBounds.start}–${sourceBounds.end} 页内，且不超过 80 页。`);
      return;
    }
    if (dirty) {
      setNotice("请先保存规划修改，再确认并开始逐页生成。");
      return;
    }
    setActionError("");
    try { await onApprove(); } catch (error) { setActionError(error instanceof Error ? error.message : "规划确认失败，请刷新后重试。"); }
  };
  const totalMinutes = draft.sessions.reduce((sum, session) => sum + session.durationMinutes, 0);
  const pageCount = draft.sessions.reduce((sum, session) => sum + session.sections.reduce((sectionSum, section) => sectionSum + section.pages.length, 0), 0);
  const rangeIsValid = validRange(draft.pageStart, draft.pageEnd, sourceBounds);

  return <main className="courseware-plan-shell">
    <header className="courseware-plan-header"><div><span className="courseware-eyebrow">STEP 2 · REVIEW THE PLAN</span><h1>确认课时与页面规划</h1><p>规划先经教师确认，系统才会开始生成课件页面。教材原文、教师讲稿和活动答案将在不同视图中展示。</p></div><div className="courseware-plan-summary"><strong>{totalMinutes} min</strong><span>{draft.sessions.length} sessions · {pageCount} pages</span></div></header>
    <div className="courseware-plan-meta"><span>规划版本 {plan.planVersion}</span><span>学科：{subjectLabel(plan.subject)}</span><span>规划状态：{plan.status === "awaiting_confirmation" ? "等待教师确认" : plan.status === "approved" ? "已确认" : plan.status === "superseded" ? "已更新" : "草稿"}</span></div>
    {plan.generationStatus && plan.generationStatus !== "completed" && <p className="courseware-plan-warning" role="status">规划任务：{plan.generationStatus}。请等待任务完成后再确认。</p>}
    <section className="courseware-plan-brief"><label>学习对象<input value={draft.audience} maxLength={200} onChange={(event) => update((previous) => ({ ...previous, audience: event.target.value }))} /></label><label>授课语言策略<input value={draft.languagePolicy} maxLength={500} onChange={(event) => update((previous) => ({ ...previous, languagePolicy: event.target.value }))} /></label><label>规划来源起始页<input type="number" min={sourceBounds.start} max={sourceBounds.end} value={draft.pageStart ?? ""} onChange={(event) => update((previous) => ({ ...previous, pageStart: event.target.value ? Number(event.target.value) : undefined }))} /></label><label>规划来源结束页<input type="number" min={sourceBounds.start} max={sourceBounds.end} value={draft.pageEnd ?? ""} onChange={(event) => update((previous) => ({ ...previous, pageEnd: event.target.value ? Number(event.target.value) : undefined }))} /></label></section>
    {!rangeIsValid && <p className="courseware-plan-warning" role="alert">请将规划范围设在教材第 {sourceBounds.start}–{sourceBounds.end} 页内，并确保不超过 80 页。</p>}
    <section className="courseware-plan-objectives"><h2>学习目标与教材覆盖</h2><p>每个目标保留来源页码；页面引用在保存时继续提交给服务端校验。</p>{draft.objectives.map((objective, index) => <article key={objective.objectiveId}><div><span>目标 {index + 1}</span><strong>{objective.sourceRefs.length ? `教材第 ${uniquePages(objective.sourceRefs).join("、")} 页` : "尚未关联教材来源"}</strong></div><label>目标描述<textarea rows={2} maxLength={500} value={objective.description} onChange={(event) => update((previous) => ({ ...previous, objectives: previous.objectives.map((item, itemIndex) => itemIndex === index ? { ...item, description: event.target.value } : item) }))} /></label></article>)}</section>
    <section className="courseware-plan-sessions"><h2>课时环节与逐页大纲</h2>{draft.sessions.map((session, sessionIndex) => <article className="courseware-plan-session" key={session.sessionId}><header><span>Session {sessionIndex + 1} · {session.durationMinutes} min</span><h3>{session.title}</h3><small>{session.sections.length} classroom sections</small></header>{session.sections.map((section, sectionIndex) => <section className="courseware-plan-section" key={section.sectionId}><header><div><span>{sectionIndex + 1} · {section.estimatedMinutes} min</span><h4>{section.title}</h4><p>{section.purpose}</p></div><strong>{section.pages.length} pages</strong></header><div className="courseware-plan-pages">{section.pages.map((page, pageIndex) => <article className="courseware-plan-page" key={page.pageId}><div className="courseware-plan-page-order">{String(pageIndex + 1).padStart(2, "0")}</div><div className="courseware-plan-page-fields"><label>页面标题<input value={page.title} maxLength={200} onChange={(event) => updatePage(page.pageId, (item) => ({ ...item, title: event.target.value }))} /></label><div className="courseware-plan-page-options"><label>页面类型<select value={page.pageKind} onChange={(event) => updatePage(page.pageId, (item) => ({ ...item, pageKind: event.target.value as CoursewarePlanPageKind }))}>{pageKinds.map((kind) => <option key={kind} value={kind}>{kind.replace(/_/g, " ")}</option>)}</select></label><label>版式<select value={page.layout} onChange={(event) => updatePage(page.pageId, (item) => ({ ...item, layout: event.target.value as typeof layouts[number] }))}>{layouts.map((layout) => <option key={layout} value={layout}>{layout.replace(/_/g, " ")}</option>)}</select></label><label className="courseware-plan-required"><input type="checkbox" checked={page.required} onChange={(event) => updatePage(page.pageId, (item) => ({ ...item, required: event.target.checked }))} />必讲页面</label></div><p className="courseware-plan-source">{page.sourceRefs.length ? `教材第 ${uniquePages(page.sourceRefs).join("、")} 页 · ${page.estimatedMinutes} 分钟` : "未关联教材来源 · 请在生成后重点复核"}</p><fieldset><legend>关联学习目标</legend>{draft.objectives.map((objective) => <label key={objective.objectiveId}><input type="checkbox" checked={(page.objectiveIds ?? []).includes(objective.objectiveId)} onChange={(event) => updatePage(page.pageId, (item) => ({ ...item, objectiveIds: event.target.checked ? [...(item.objectiveIds ?? []), objective.objectiveId] : (item.objectiveIds ?? []).filter((id) => id !== objective.objectiveId) }))} />{objective.description}</label>)}</fieldset></div></article>)}</div></section>)}</article>)}</section>
    <footer className="courseware-plan-actions"><div>{notice && <p role="status">{notice}</p>}{actionError && <p role="alert">{actionError}</p>}<p>确认后将按当前规划逐页生成；页面失败可以单独重试，不会重做已成功页面。</p></div><button type="button" disabled={busy || !dirty || !draft.audience.trim() || !draft.languagePolicy.trim() || !validRange(draft.pageStart, draft.pageEnd, sourceBounds)} onClick={() => void save()}>{busy ? "正在保存…" : dirty ? "保存规划修改" : "规划已保存"}</button><button type="button" className="is-primary" disabled={busy || plan.status === "approved" || plan.generationStatus === "queued" || plan.generationStatus === "running" || plan.generationStatus === "failed" || !draft.objectives.length || pageCount === 0 || !validRange(draft.pageStart, draft.pageEnd, sourceBounds)} onClick={() => void approve()}>{busy ? "正在启动…" : "确认规划并生成课件"}</button></footer>
  </main>;
}

function uniquePages(refs: Array<{ page: number }>) {
  return [...new Set(refs.map((reference) => reference.page))].sort((left, right) => left - right);
}

function subjectLabel(subject: string) {
  if (subject === "english") return "英语";
  if (subject === "math") return "数学";
  return subject || "未配置学科";
}

function validRange(start: number | null | undefined, end: number | null | undefined, bounds: { start: number; end: number }) {
  return typeof start === "number" && typeof end === "number" && Number.isInteger(start) && Number.isInteger(end)
    && start >= bounds.start && end <= bounds.end && end >= start && end - start + 1 <= 80;
}
