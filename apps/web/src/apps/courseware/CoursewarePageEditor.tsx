import { Button } from "../../components/ui";
import { useEffect, useState } from "react";
import { editablePage, type CoursewareAuthoringPage, type CoursewareEditableBlock, type CoursewarePageEditView } from "./coursewareAuthoringView";

const pageKinds = ["cover", "explanation", "vocabulary", "reading", "worked_example", "activity", "practice", "summary"] as const;
const layouts = ["hero_split", "two_column", "cards", "text_focus", "figure_focus"] as const;

export function CoursewarePageEditor({
  page,
  busy,
  onSave,
  onRegenerate,
  onReview,
  onDirtyChange,
}: {
  page: CoursewareAuthoringPage;
  busy: boolean;
  onSave: (pageId: string, value: CoursewarePageEditView) => Promise<void>;
  onRegenerate: (pageId: string, reason: string) => Promise<void>;
  onReview: (pageId: string, decision: "approve" | "request_changes", note: string) => Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [draft, setDraft] = useState<CoursewarePageEditView>(() => editablePage(page));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const [reason, setReason] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [actionError, setActionError] = useState("");
  const [regenerateOpen, setRegenerateOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const canReview = page.generationStatus === "completed" && page.blocks.length > 0 && !page.error;

  const update = (change: (previous: CoursewarePageEditView) => CoursewarePageEditView) => {
    setDraft((previous) => change(previous));
    setDirty(true);
  };
  const changeBlock = (index: number, change: (block: CoursewareEditableBlock) => CoursewareEditableBlock) => update((previous) => ({
    ...previous,
    blocks: (previous.blocks ?? []).map((block, blockIndex) => blockIndex === index ? change(block) : block),
  }));
  const save = async () => {
    setActionError("");
    try {
      await onSave(page.pageId, draft);
      setDirty(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "页面保存失败，请重试。");
    }
  };
  const regenerate = async () => {
    if (!reason.trim()) return;
    setActionError("");
    try {
      await onRegenerate(page.pageId, reason.trim());
      setReason("");
      setRegenerateOpen(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "重做任务未能启动。");
    }
  };
  const review = async (decision: "approve" | "request_changes") => {
    if (decision === "request_changes" && !reviewNote.trim()) return;
    setActionError("");
    try {
      await onReview(page.pageId, decision, reviewNote.trim());
      setReviewOpen(false);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "审核结果未能保存。");
    }
  };

  return <section className="courseware-page-editor" aria-label={`编辑页面：${page.title}`}>
    <div className="courseware-editor-page-heading">
      <div><span className="courseware-eyebrow">{page.pageKind.replace(/_/g, " ")} · {page.generationStatus} · {page.reviewStatus}</span><h2>{page.title}</h2></div>
      <span className={`courseware-page-status is-${page.generationStatus}`}>{page.generationStatus === "completed" ? "生成完成" : page.generationStatus === "failed" ? "生成失败" : page.generationStatus === "running" ? "生成中" : page.generationStatus}</span>
    </div>
    {page.error && <p className="courseware-editor-error" role="alert">{page.error}</p>}
    {actionError && <p className="courseware-editor-error" role="alert">{actionError}</p>}
    {!canReview && <p className="courseware-editor-warning" role="status">页面生成完成且包含可核对内容后，才能进入教师审核。</p>}
    <div className="courseware-editor-fields">
      <label>页面标题<input value={draft.title} maxLength={200} onChange={(event) => update((previous) => ({ ...previous, title: event.target.value }))} /></label>
      <label>页面类型<select value={draft.pageKind} onChange={(event) => update((previous) => ({ ...previous, pageKind: event.target.value as typeof pageKinds[number] }))}>{pageKinds.map((kind) => <option key={kind} value={kind}>{kind.replace(/_/g, " ")}</option>)}</select></label>
      <label>版式<select value={draft.layout} onChange={(event) => update((previous) => ({ ...previous, layout: event.target.value as typeof layouts[number] }))}>{layouts.map((layout) => <option key={layout} value={layout}>{layout.replace(/_/g, " ")}</option>)}</select></label>
      <label>预计用时（分钟）<input type="number" min="1" max="30" value={draft.estimatedMinutes} onChange={(event) => update((previous) => ({ ...previous, estimatedMinutes: Number(event.target.value) }))} /></label>
    </div>
    <details className="courseware-editor-sources"><summary>教材来源（{draft.sourceRefs.length} 处）</summary><ul>{draft.sourceRefs.map((reference, index) => <li key={`${reference.sourceRevisionId}-${reference.page}-${index}`}>教材第 {reference.page} 页 · {reference.sourceRevisionId}</li>)}</ul></details>
    <label className="courseware-editor-notes">教师讲稿<textarea rows={4} maxLength={4000} value={draft.teacherNotes ?? ""} onChange={(event) => update((previous) => ({ ...previous, teacherNotes: event.target.value }))} placeholder="讲课提示、提问和时间安排仅教师可见" /></label>
    <section className="courseware-editor-blocks"><h3>页面内容</h3>{(draft.blocks ?? []).map((block, index) => <BlockEditor key={blockKey(block)} page={page} block={block} onChange={(change) => changeBlock(index, change)} />)}</section>
    <section className="courseware-editor-reveals"><h3>按步揭晓（{draft.revealSteps?.length ?? 0} 步）</h3>{(draft.revealSteps ?? []).map((step, index) => <fieldset key={step.revealId}><legend>{step.label || `第 ${index + 1} 步`}</legend><label>显示标签<input value={step.label} onChange={(event) => update((previous) => ({ ...previous, revealSteps: previous.revealSteps?.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item) }))} /></label><label>揭晓内容<textarea rows={3} value={step.content} onChange={(event) => update((previous) => ({ ...previous, revealSteps: previous.revealSteps?.map((item, itemIndex) => itemIndex === index ? { ...item, content: event.target.value } : item) }))} /></label></fieldset>)}</section>
    <div className="courseware-editor-actions">
      <Button type="button" className="is-primary" disabled={busy || !dirty || !draft.title.trim() || draft.estimatedMinutes < 1} onClick={() => void save()}>{busy ? "正在保存…" : dirty ? "保存页面修改" : "页面已保存"}</Button>
      <Button type="button" disabled={busy || dirty || page.generationStatus === "running" || page.generationStatus === "queued"} onClick={() => setRegenerateOpen((open) => !open)}>重做本页</Button>
      <Button type="button" disabled={busy || dirty || !canReview || page.reviewStatus === "approved"} onClick={() => void review("approve")}>审核通过</Button>
      <Button type="button" disabled={busy || dirty || !canReview} onClick={() => setReviewOpen((open) => !open)}>请求修改</Button>
    </div>
    {regenerateOpen && <div className="courseware-editor-command"><label>重做原因<textarea rows={2} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明本页需要修正的内容" /></label><Button type="button" disabled={busy || !reason.trim()} onClick={() => void regenerate()}>确认重做这一页</Button><Button type="button" onClick={() => setRegenerateOpen(false)}>取消</Button></div>}
    {reviewOpen && <div className="courseware-editor-command"><label>审核意见<textarea rows={2} maxLength={2000} value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="指出需要补充或核对的地方" /></label><Button type="button" disabled={busy || !reviewNote.trim()} onClick={() => void review("request_changes")}>提交修改意见</Button><Button type="button" onClick={() => setReviewOpen(false)}>取消</Button></div>}
  </section>;
}

function blockKey(block: CoursewareEditableBlock) {
  return block.blockId;
}

function BlockEditor({ page, block, onChange }: { page: CoursewareAuthoringPage; block: CoursewareEditableBlock; onChange: (change: (block: CoursewareEditableBlock) => CoursewareEditableBlock) => void }) {
  if (block.kind === "text") return <fieldset className="courseware-block-editor"><legend>正文</legend><label>讲解内容<textarea rows={4} value={block.text} onChange={(event) => onChange((current) => current.kind === "text" ? { ...current, text: event.target.value } : current)} /></label><SourceSummary page={page} /></fieldset>;
  if (block.kind === "image") return <fieldset className="courseware-block-editor"><legend>教材图片</legend><p>图片由教材受控素材提供；无法在此处替换为外部图片。</p><label>图片说明<input value={block.alt} onChange={(event) => onChange((current) => current.kind === "image" ? { ...current, alt: event.target.value } : current)} /></label><label>图注<input value={block.caption ?? ""} onChange={(event) => onChange((current) => current.kind === "image" ? { ...current, caption: event.target.value } : current)} /></label></fieldset>;
  if (block.kind === "formula") return <fieldset className="courseware-block-editor"><legend>公式</legend><label>LaTeX<input value={block.latex} onChange={(event) => onChange((current) => current.kind === "formula" ? { ...current, latex: event.target.value } : current)} /></label><label>说明<textarea rows={2} value={block.explanation ?? ""} onChange={(event) => onChange((current) => current.kind === "formula" ? { ...current, explanation: event.target.value } : current)} /></label><SourceSummary page={page} /></fieldset>;
  if (block.kind === "word_cards") return <fieldset className="courseware-block-editor"><legend>词卡 · {block.cards.length} 张</legend>{block.cards.map((card, index) => <div className="courseware-editor-fields" key={`${page.pageId}-card-${index}`}><label>词语<input value={card.term} onChange={(event) => onChange((current) => current.kind === "word_cards" ? { ...current, cards: current.cards.map((item, itemIndex) => itemIndex === index ? { ...item, term: event.target.value } : item) } : current)} /></label><label>解释<input value={card.meaning} onChange={(event) => onChange((current) => current.kind === "word_cards" ? { ...current, cards: current.cards.map((item, itemIndex) => itemIndex === index ? { ...item, meaning: event.target.value } : item) } : current)} /></label></div>)}</fieldset>;
  if (block.kind === "input") return <fieldset className="courseware-block-editor"><legend>课堂填写</legend><label>提示语<input value={block.prompt} onChange={(event) => onChange((current) => current.kind === "input" ? { ...current, prompt: event.target.value } : current)} /></label><label>需要填写数量<input type="number" min="1" max="20" value={block.minResponses} onChange={(event) => onChange((current) => current.kind === "input" ? { ...current, minResponses: Number(event.target.value) } : current)} /></label></fieldset>;
  if (block.kind === "checklist") return <fieldset className="courseware-block-editor"><legend>同伴检查</legend><label>标题<input value={block.title} onChange={(event) => onChange((current) => current.kind === "checklist" ? { ...current, title: event.target.value } : current)} /></label>{block.items.map((item, index) => <label key={item.itemId}>检查项 {index + 1}<input value={item.label} onChange={(event) => onChange((current) => current.kind === "checklist" ? { ...current, items: current.items.map((value, itemIndex) => itemIndex === index ? { ...value, label: event.target.value } : value) } : current)} /></label>)}</fieldset>;
  return <fieldset className="courseware-block-editor"><legend>{block.assessmentMode === "participation" ? "课堂活动" : "可选练习"}</legend><label>活动说明<textarea rows={3} value={block.instructions} onChange={(event) => onChange((current) => current.kind === "activity" ? { ...current, instructions: event.target.value } : current)} /></label><label>选项（每行一个）<textarea rows={3} value={block.choices?.join("\n") ?? ""} onChange={(event) => onChange((current) => current.kind === "activity" ? { ...current, choices: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) } : current)} /></label>{block.answerKey !== undefined && <label>示范答案（仅教师可见）<textarea rows={2} value={Array.isArray(block.answerKey) ? block.answerKey.join("\n") : block.answerKey ?? ""} onChange={(event) => onChange((current) => current.kind === "activity" ? { ...current, answerKey: event.target.value } : current)} /></label>}{block.teacherRubric != null && <label>教师评分建议<textarea rows={2} value={block.teacherRubric} onChange={(event) => onChange((current) => current.kind === "activity" ? { ...current, teacherRubric: event.target.value } : current)} /></label>}</fieldset>;
}

function SourceSummary({ page }: { page: CoursewareAuthoringPage }) {
  return <p className="courseware-editor-source">{page.sourceRefs.length ? `本页来源：${[...new Set(page.sourceRefs.map((reference) => reference.page))].join("、")} 页` : "尚未关联教材原文。请复核来源后再批准页面。"}</p>;
}
