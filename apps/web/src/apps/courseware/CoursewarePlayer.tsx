import { useEffect, useMemo, useState, type ReactNode } from "react";
import { apiResourceUrl } from "../../api/client";
import MathText from "../../MathText";
import { RichText } from "../../RichText";
import type { CoursewareBlockView, CoursewarePageView, CoursewarePresentationDocument } from "./coursewarePresentation";
import { useCoursewarePlayback } from "./useCoursewarePlayback";
import "./courseware.css";

type Panel = "content" | "notes" | "answers";

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}

function subjectLabel(subject: string) {
  if (subject === "english") return "英语";
  if (subject === "math") return "数学";
  if (subject === "science") return "科学";
  return subject.trim() || "未配置学科";
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || Boolean(target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']"));
}

function blockActivityKey(page: CoursewarePageView, blockIndex: number, block: CoursewareBlockView) {
  if (block.kind === "activity") return `${page.pageId}:${block.activityId}`;
  if (block.kind === "input" || block.kind === "checklist") return `${page.pageId}:${block.blockId}`;
  return `${page.pageId}:block-${blockIndex}`;
}

export function CoursewarePlayer({ document: lesson, now = Date.now, onSubmitActivity, playbackScope = "teacher:local", renderPageTools }: {
  document: CoursewarePresentationDocument;
  now?: () => number;
  renderPageTools?: (page: CoursewarePageView) => ReactNode;
  /** UI identity scope prevents classroom state from crossing learner or role boundaries. */
  playbackScope?: string;
  onSubmitActivity?: (activityId: string, response: { text?: string; selectedOptions?: string[] }) => Promise<{ assessment: string; feedback: string }>;
}) {
  const playback = useCoursewarePlayback(lesson, now, playbackScope);
  const { page, pageIndex, section, state } = playback;
  const [fullscreen, setFullscreen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [submittingActivityId, setSubmittingActivityId] = useState("");
  const [attemptFeedback, setAttemptFeedback] = useState<Record<string, string>>({});
  const assetsById = useMemo(() => new Map(lesson.assets.map((asset) => [asset.assetId, asset])), [lesson.assets]);
  const currentReveals = page?.revealSteps ?? [];
  const revealedIds = page ? state.revealed[page.pageId] ?? [] : [];
  const nextReveal = currentReveals.find((step) => !revealedIds.includes(step.revealId));
  const activePanel = lesson.canRevealAnswers ? state.panel : "content";

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || isEditableTarget(event.target)) return;
      if (event.key === "ArrowLeft" && pageIndex > 0) {
        event.preventDefault();
        playback.navigateTo(lesson.pages[pageIndex - 1].pageId);
      } else if (event.key === "ArrowRight" && pageIndex < lesson.pages.length - 1) {
        event.preventDefault();
        playback.navigateTo(lesson.pages[pageIndex + 1].pageId);
      } else if (event.key.toLowerCase() === "r" && lesson.canRevealAnswers && nextReveal) {
        event.preventDefault();
        playback.revealNext();
      } else if (event.key.toLowerCase() === "m" && lesson.canRevealAnswers && page?.teacherNotes) {
        event.preventDefault();
        playback.setPanel(activePanel === "notes" ? "content" : "notes");
      } else if (event.key.toLowerCase() === "t") {
        event.preventDefault();
        playback.startOrPauseTimer();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activePanel, lesson.canRevealAnswers, lesson.pages, nextReveal, page?.teacherNotes, pageIndex, playback]);

  useEffect(() => {
    const onFullscreenChange = () => setFullscreen(Boolean(window.document.fullscreenElement));
    window.document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => window.document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  if (!page) return <main className="courseware-player courseware-empty"><p>这份课件还没有可播放的页面。</p></main>;

  const jumpToSection = (sectionId: string) => {
    const target = lesson.pages.find((item) => item.sectionId === sectionId);
    if (target) playback.navigateTo(target.pageId);
  };
  const movePage = (offset: number) => {
    const target = lesson.pages[pageIndex + offset];
    if (target) playback.navigateTo(target.pageId);
  };
  const toggleFullscreen = async () => {
    if (window.document.fullscreenElement) {
      await window.document.exitFullscreen?.();
      setFullscreen(false);
      return;
    }
    try {
      await documentRoot().requestFullscreen?.();
      setFullscreen(Boolean(window.document.fullscreenElement));
    } catch {
      setFullscreen((value) => !value);
    }
  };
  const resetCurrent = () => {
    if (window.confirm("只清空当前页的输入、同伴检查和答案揭晓状态吗？")) playback.resetCurrentPage();
  };
  const resetAll = () => {
    if (window.confirm("清空本次授课的所有输入、检查项、答案揭晓和计时器吗？课件内容与发布版本不会改变。")) playback.resetSession();
  };
  const submitActivity = async (block: Extract<CoursewareBlockView, { kind: "activity" }>, pageValue: CoursewarePageView, blockIndex: number) => {
    if (!onSubmitActivity) return;
    const key = blockActivityKey(pageValue, blockIndex, block);
    const selected = playback.selectedChoices(key);
    const text = playback.activityResponse(key);
    if (!selected.length && !text.trim()) return;
    setSubmittingActivityId(block.activityId);
    setAttemptFeedback({});
    try {
      const result = await onSubmitActivity(block.activityId, block.choices.length > 0 ? { selectedOptions: selected } : { text });
      setAttemptFeedback({ [block.activityId]: `${result.assessment} · ${result.feedback}` });
    } catch (error) {
      setAttemptFeedback({ [block.activityId]: error instanceof Error ? error.message : "提交失败，请稍后重试。" });
    } finally {
      setSubmittingActivityId("");
    }
  };

  return <main className={`courseware-player ${state.highContrast ? "is-high-contrast" : ""} ${state.mode === "rail" ? "is-rail" : "is-deck"} ${fullscreen ? "is-fullscreen" : ""}`}>
    <header className="courseware-toolbar">
      <div className="courseware-brand"><span className="courseware-brand-mark" aria-hidden="true">DT</span><div><strong>{lesson.title}</strong><span>{subjectLabel(lesson.subject)} · 第 {lesson.version} 版</span></div></div>
      <div className="courseware-tools" aria-label="授课工具">
        <button type="button" className="courseware-timer" aria-label={state.timerDeadline === null ? state.timerSeconds === 0 ? "Restart timer" : "Start timer" : "Pause timer"} title={state.timerSeconds === 0 && state.timerDeadline === null ? "倒计时已结束；点击重新开始" : undefined} onClick={playback.startOrPauseTimer}>
          <span>{state.timerSeconds === 0 && state.timerDeadline === null ? "Restart timer" : "Timer"}</span><time>{formatTime(state.timerSeconds)}</time>
        </button>
        <button type="button" onClick={playback.revealNext} disabled={!lesson.canRevealAnswers || !nextReveal}>Show answers</button>
        <details className="courseware-reset-menu"><summary>Reset</summary><div><button type="button" onClick={resetCurrent}>Reset this page</button><button type="button" onClick={resetAll}>Reset this session</button></div></details>
        <span className="courseware-page-count" aria-label={`Page ${pageIndex + 1} of ${lesson.pages.length}`}>{pageIndex + 1} / {lesson.pages.length}</span>
        <button type="button" aria-pressed={state.mode === "deck"} onClick={() => playback.setMode("deck")}>Deck</button>
        <button type="button" aria-pressed={state.mode === "rail"} onClick={() => playback.setMode("rail")}>Rail</button>
        <button type="button" onClick={() => playback.setPanel("notes")} disabled={!page.teacherNotes}>Teacher notes</button>
        <button type="button" aria-pressed={state.highContrast} onClick={playback.toggleContrast}>Contrast</button>
        <button type="button" aria-pressed={fullscreen} onClick={() => void toggleFullscreen()}>{fullscreen ? "Exit full screen" : "Full screen"}</button>
        <button type="button" aria-expanded={helpOpen} onClick={() => setHelpOpen((open) => !open)}>Help</button>
      </div>
    </header>

    {helpOpen && <div className="courseware-help" role="status">Use ← / → to change pages, R to reveal the next answer, M for teacher notes, and T to pause or resume the timer. Shortcuts stay inactive while you type.</div>}

    <div className="courseware-workspace">
      {state.mode === "rail" && <nav className="courseware-rail" aria-label="Page rail">{lesson.pages.map((item, index) => <button type="button" key={item.pageId} aria-current={item.pageId === page.pageId ? "page" : undefined} onClick={() => playback.navigateTo(item.pageId)}><span>{index + 1}</span><strong>{item.title}</strong></button>)}</nav>}
      <section className="courseware-stage" aria-label="Presentation page">
        {page.pageKind === "cover" ? <CoverPage document={lesson} page={page} sectionTitle={section?.title ?? ""} assetsById={assetsById} panel={activePanel} setPanel={playback.setPanel} revealedIds={revealedIds} nextRevealId={nextReveal?.revealId} onReveal={playback.revealStep} playback={playback} onSubmitActivity={onSubmitActivity ? submitActivity : undefined} submittingActivityId={submittingActivityId} attemptFeedback={attemptFeedback} /> : <>
          <div className="courseware-page-meta"><span className="courseware-section-chip">{section?.title ?? "Class"}</span><span>{String((lesson.sections.findIndex((item) => item.sectionId === page.sectionId) + 1)).padStart(2, "0")} / {String(lesson.sections.length).padStart(2, "0")}</span><span>{section?.estimatedMinutes ?? page.estimatedMinutes} min</span></div>
          <div className={`courseware-content-layout ${page.layout === "text_focus" || !page.blocks.some((block) => block.kind === "image") ? "is-text-focus" : ""}`}>
            <article className="courseware-page-copy">
              <h1>{page.title}</h1>
              <PageTabs panel={activePanel} setPanel={playback.setPanel} hasNotes={lesson.canRevealAnswers && Boolean(page.teacherNotes)} canRevealAnswers={lesson.canRevealAnswers} />
              {activePanel === "content" && <PageContent page={page} assetsById={assetsById} playback={playback} onSubmitActivity={onSubmitActivity ? submitActivity : undefined} submittingActivityId={submittingActivityId} attemptFeedback={attemptFeedback} />}
              {activePanel === "notes" && <div className="courseware-notes" role="tabpanel" aria-label="Teacher notes"><span className="courseware-eyebrow">Teacher notes · private</span><p><RichText text={page.teacherNotes || "No notes for this page."} /></p></div>}
              {activePanel === "answers" && <AnswerPanel page={page} revealedIds={revealedIds} nextRevealId={nextReveal?.revealId} onReveal={playback.revealStep} canReveal={lesson.canRevealAnswers} />}
            </article>
            {activePanel === "content" && <PageImages page={page} assetsById={assetsById} />}
          </div>
        </>}
        {renderPageTools?.(page)}
        <SourceFooter page={page} document={lesson} />
      </section>
    </div>

    <section className="courseware-timeline" aria-label="Class timeline">
      <div className="courseware-timeline-heading"><span>CLASS RUN</span><strong>{section?.title ?? "Lesson"}</strong><span>·</span><span>{lesson.durationMinutes} min total</span><span>Click a section to jump</span></div>
      <nav aria-label="Class timeline sections">{lesson.sections.map((item, index) => <button type="button" key={item.sectionId} aria-current={item.sectionId === section?.sectionId ? "step" : undefined} aria-label={`${item.title} · ${item.estimatedMinutes} min`} onClick={() => jumpToSection(item.sectionId)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong><small>{item.estimatedMinutes}′</small></button>)}</nav>
    </section>

    <footer className="courseware-pagination">
      <button type="button" onClick={() => movePage(-1)} disabled={pageIndex <= 0}>‹ Prev</button>
      <div className="courseware-progress-dots" aria-label="Page progress">{lesson.pages.map((item, index) => <button key={item.pageId} type="button" aria-label={`Go to page ${index + 1}`} aria-current={item.pageId === page.pageId ? "step" : undefined} onClick={() => playback.navigateTo(item.pageId)} />)}</div>
      <div className="courseware-progress-track" role="progressbar" aria-label="Lesson progress" aria-valuemin={0} aria-valuemax={lesson.pages.length} aria-valuenow={pageIndex + 1}><span style={{ width: `${((pageIndex + 1) / lesson.pages.length) * 100}%` }} /></div>
      <span>{String(pageIndex + 1).padStart(2, "0")} / {String(lesson.pages.length).padStart(2, "0")}</span>
      <button type="button" onClick={() => movePage(1)} disabled={pageIndex >= lesson.pages.length - 1}>Next ›</button>
    </footer>
  </main>;
}

function documentRoot() {
  return window.document.querySelector(".courseware-player") ?? window.document.documentElement;
}

function PageTabs({ panel, setPanel, hasNotes, canRevealAnswers }: { panel: Panel; setPanel: (panel: Panel) => void; hasNotes: boolean; canRevealAnswers: boolean }) {
  return <div className="courseware-tabs" role="tablist" aria-label="Page view">
    <button type="button" role="tab" aria-selected={panel === "content"} onClick={() => setPanel("content")}>Page</button>
    {hasNotes && <button type="button" role="tab" aria-selected={panel === "notes"} onClick={() => setPanel("notes")}>Teacher notes</button>}
    {canRevealAnswers && <button type="button" role="tab" aria-selected={panel === "answers"} onClick={() => setPanel("answers")}>Answers</button>}
  </div>;
}

function CoverPage({ document: lesson, page, sectionTitle, assetsById, panel, setPanel, revealedIds, nextRevealId, onReveal, playback, onSubmitActivity, submittingActivityId, attemptFeedback }: {
  document: CoursewarePresentationDocument;
  page: CoursewarePageView;
  sectionTitle: string;
  assetsById: Map<string, CoursewarePresentationDocument["assets"][number]>;
  panel: Panel;
  setPanel: (panel: Panel) => void;
  revealedIds: string[];
  nextRevealId?: string;
  onReveal: (revealId: string) => void;
  playback: ReturnType<typeof useCoursewarePlayback>;
  onSubmitActivity?: (block: Extract<CoursewareBlockView, { kind: "activity" }>, page: CoursewarePageView, index: number) => void;
  submittingActivityId: string;
  attemptFeedback: Record<string, string>;
}) {
  const textBlocks = page.blocks.flatMap((block, index) => block.kind !== "image" ? [{ block, index }] : []);
  const image = page.blocks.find((block) => block.kind === "image");
  return <div className={`courseware-cover ${image?.kind === "image" ? "" : "is-no-image"}`}>
    <div className="courseware-cover-copy"><span className="courseware-eyebrow">{sectionTitle} · {lesson.sourceTitle || "Selected textbook"}</span><h1>{lesson.title}</h1><p className="courseware-cover-subtitle">{subjectLabel(lesson.subject)} · {page.estimatedMinutes} min opening</p>
      <PageTabs panel={panel} setPanel={setPanel} hasNotes={lesson.canRevealAnswers && Boolean(page.teacherNotes)} canRevealAnswers={lesson.canRevealAnswers} />
      {panel === "content" && <>
        {textBlocks.map(({ block, index }) => block.kind === "text" ? <p className="courseware-cover-intro" key={`${page.pageId}-text-${index}`}><RichText text={block.text} /></p> : <BlockView key={`${page.pageId}-block-${index}`} page={page} block={block} blockIndex={index} assetsById={assetsById} playback={playback} onSubmitActivity={onSubmitActivity} submittingActivityId={submittingActivityId} attemptFeedback={attemptFeedback} />)}
        {lesson.sections.length > 0 && <div className="courseware-goals"><span className="courseware-eyebrow">Today's path</span><p>{lesson.sections.map((item) => `${item.title} · ${item.estimatedMinutes}′`).join("  /  ")}</p></div>}
      </>}
      {panel === "notes" && lesson.canRevealAnswers && <div className="courseware-notes" role="tabpanel" aria-label="Teacher notes"><span className="courseware-eyebrow">Teacher notes · private</span><p><RichText text={page.teacherNotes || "No notes for this page."} /></p></div>}
      {panel === "answers" && lesson.canRevealAnswers && <AnswerPanel page={page} revealedIds={revealedIds} nextRevealId={nextRevealId} onReveal={onReveal} canReveal />}
    </div>
    {image?.kind === "image" && <AssetFigure asset={assetsById.get(image.assetId)} alt={image.alt} caption={image.caption} />}
  </div>;
}

function PageContent({ page, assetsById, playback, onSubmitActivity, submittingActivityId, attemptFeedback }: {
  page: CoursewarePageView;
  assetsById: Map<string, CoursewarePresentationDocument["assets"][number]>;
  playback: ReturnType<typeof useCoursewarePlayback>;
  onSubmitActivity?: (block: Extract<CoursewareBlockView, { kind: "activity" }>, page: CoursewarePageView, index: number) => void;
  submittingActivityId: string;
  attemptFeedback: Record<string, string>;
}) {
  return <div className="courseware-blocks">{page.blocks.map((block, index) => block.kind === "image" ? null : <BlockView key={`${page.pageId}-block-${index}`} page={page} block={block} blockIndex={index} assetsById={assetsById} playback={playback} onSubmitActivity={onSubmitActivity} submittingActivityId={submittingActivityId} attemptFeedback={attemptFeedback} />)}</div>;
}

function BlockView({ page, block, blockIndex, assetsById, playback, onSubmitActivity, submittingActivityId, attemptFeedback }: {
  page: CoursewarePageView;
  block: CoursewareBlockView;
  blockIndex: number;
  assetsById: Map<string, CoursewarePresentationDocument["assets"][number]>;
  playback: ReturnType<typeof useCoursewarePlayback> | null;
  onSubmitActivity?: (block: Extract<CoursewareBlockView, { kind: "activity" }>, page: CoursewarePageView, index: number) => void;
  submittingActivityId?: string;
  attemptFeedback?: Record<string, string>;
}) {
  if (block.kind === "text") return <div className="courseware-text-block"><RichText text={block.text} />{block.sourceRefs.length > 0 && <SourcePages refs={block.sourceRefs} />}</div>;
  if (block.kind === "image") return <AssetFigure asset={assetsById.get(block.assetId)} alt={block.alt} caption={block.caption} />;
  if (block.kind === "formula") return <div className="courseware-formula-block"><MathText block text={`$$${block.latex}$$`} />{block.explanation && <p><RichText text={block.explanation} /></p>}{block.sourceRefs.length > 0 && <SourcePages refs={block.sourceRefs} />}</div>;
  if (block.kind === "word_cards") return <div className="courseware-word-cards">{block.cards.map((card, index) => <article key={`${page.pageId}-word-${index}`}><strong>{card.term}</strong><p>{card.meaning}</p>{card.example && <blockquote><RichText text={card.example} /></blockquote>}{card.sourceRefs.length > 0 && <SourcePages refs={card.sourceRefs} />}</article>)}</div>;
  if (block.kind === "input") {
    const key = blockActivityKey(page, blockIndex, block);
    const values = playback?.inputResponses(key) ?? [];
    return <section className="courseware-activity-block"><div className="courseware-activity-heading"><div><span className="courseware-eyebrow">Write first</span><h2>{block.prompt}</h2></div><span className="courseware-written-count">{playback?.writtenCount(key) ?? 0} / {block.minResponses} written</span></div>
      {Array.from({ length: block.minResponses }, (_, index) => <label className="courseware-response-row" key={`${key}-response-${index}`}><span>{index + 1}</span><input aria-label={`Observation ${index + 1}`} value={values[index] ?? ""} placeholder={block.placeholder || "Write a thought"} onChange={(event) => playback?.setResponse(key, index, event.target.value)} /></label>)}
    </section>;
  }
  if (block.kind === "checklist") {
    const key = blockActivityKey(page, blockIndex, block);
    return <section className="courseware-activity-block"><h2>{block.title || "Then share"}</h2><div className="courseware-checklist">{block.items.map((item) => <label key={item.itemId}><input type="checkbox" checked={playback?.isChecked(key, item.itemId) ?? false} onChange={() => playback?.toggleCheck(key, item.itemId)} /><span>{item.label}</span></label>)}</div></section>;
  }
  if (block.kind === "activity") {
    const key = blockActivityKey(page, blockIndex, block);
    const selected = playback?.selectedChoices(key) ?? [];
    const responseText = playback?.activityResponse(key) ?? "";
    return <section className="courseware-activity-block courseware-practice-block"><span className="courseware-eyebrow">{block.assessmentMode === "participation" ? "Class activity" : "Optional practice"}</span><h2>{block.instructions}</h2>
      {block.practiceReason && <p className="courseware-practice-reason">Why now · {block.practiceReason}</p>}
      {block.choices.length > 0 && <div className="courseware-choice-list">{block.choices.map((choice, index) => <button key={`${block.activityId}-choice-${index}`} type="button" aria-pressed={selected.includes(choice)} onClick={() => playback?.setChoice(key, choice, block.selectionMode === "multiple")}>{choice}</button>)}</div>}
      {block.choices.length === 0 && <textarea aria-label="Activity response" placeholder="Write or discuss your response" value={responseText} onChange={(event) => playback?.setResponse(key, 0, event.target.value)} />}
      {onSubmitActivity && <button type="button" aria-label={`Submit practice: ${block.instructions}`} disabled={submittingActivityId === block.activityId || (block.choices.length > 0 ? !selected.length : !responseText.trim())} onClick={() => onSubmitActivity(block, page, blockIndex)}>{submittingActivityId === block.activityId ? "Submitting…" : "Submit optional practice"}</button>}
      {attemptFeedback?.[block.activityId] && <p role="status">{attemptFeedback[block.activityId]}</p>}
      <p className="courseware-practice-hint">This activity can be skipped during the lesson.</p>
    </section>;
  }
  return null;
}

function PageImages({ page, assetsById }: { page: CoursewarePageView; assetsById: Map<string, CoursewarePresentationDocument["assets"][number]> }) {
  const imageBlocks = page.blocks.flatMap((block) => block.kind === "image" ? [block] : []);
  if (imageBlocks.length === 0) return null;
  return <aside className="courseware-page-visuals" aria-label="Textbook images">{imageBlocks.map((block, index) => <AssetFigure key={`${page.pageId}-asset-${index}`} asset={assetsById.get(block.assetId)} alt={block.alt} caption={block.caption} />)}</aside>;
}

function AssetFigure({ asset, alt, caption }: { asset?: CoursewarePresentationDocument["assets"][number]; alt: string; caption?: string }) {
  const validSrc = asset?.src?.startsWith("/api/") ? apiResourceUrl(asset.src) : undefined;
  return <figure className={`courseware-asset ${validSrc && asset?.status === "available" ? "" : "is-unavailable"}`}>
    {validSrc && asset?.status === "available" ? <img src={validSrc} alt={alt || asset.alt} /> : <div className="courseware-asset-unavailable"><p>教材图片待检查</p></div>}
    {(caption || asset?.caption) && <figcaption>{caption || asset?.caption}{asset?.sourceRefs.length ? ` · textbook p. ${asset.sourceRefs.map((ref) => ref.page).join(", ")}` : ""}</figcaption>}
  </figure>;
}

function AnswerPanel({ page, revealedIds, nextRevealId, onReveal, canReveal }: { page: CoursewarePageView; revealedIds: string[]; nextRevealId?: string; onReveal: (revealId: string) => void; canReveal: boolean }) {
  if (!canReveal) return null;
  const steps = page.revealSteps ?? [];
  const activities = page.blocks.filter((block) => block.kind === "activity");
  return <div className="courseware-answer-view" role="tabpanel" aria-label="Answers">
    <span className="courseware-eyebrow">Teacher view · reveal when ready</span>
    {steps.length === 0 && activities.length === 0 && <p>No answer or example is attached to this page.</p>}
    {steps.map((step) => {
      const revealed = revealedIds.includes(step.revealId);
      return <article key={step.revealId} className={`courseware-reveal-step ${revealed ? "is-revealed" : ""}`}>
        <div><span>{step.label}</span>{revealed ? <p><RichText text={step.content} /></p> : <p className="courseware-answer-hidden">Hidden until you reveal it.</p>}</div>
        {!revealed && step.revealId === nextRevealId && <button type="button" aria-label={`Reveal ${step.label}`} onClick={() => onReveal(step.revealId)}>Reveal</button>}
      </article>;
    })}
    {activities.map((block) => block.kind === "activity" && <article className="courseware-private-answer" key={block.activityId}><span className="courseware-eyebrow">Teacher-only example · {block.instructions}</span>
      {block.answerKey && <p><strong>Answer:</strong> <RichText text={block.answerKey} /></p>}
      {block.acceptedAnswers?.length ? <p><strong>Accepted answers:</strong> {block.acceptedAnswers.join(" · ")}</p> : null}
      {block.teacherRubric && <p><strong>Teacher review:</strong> <RichText text={block.teacherRubric} /></p>}
      {!block.answerKey && !block.acceptedAnswers?.length && !block.teacherRubric && <p>This activity is reviewed through participation or teacher discussion.</p>}
    </article>)}
  </div>;
}

function SourceFooter({ page, document: lesson }: { page: CoursewarePageView; document: CoursewarePresentationDocument }) {
  if (page.sourceRefs.length === 0 && !lesson.attributionText && !lesson.sourceTitle && !lesson.license) return null;
  const sourceUrl = safeSourceUrl(lesson.sourceUrl);
  const scopeLabel = lesson.distributionScope ? { private: "Private", classroom: "Classroom", public: "Public" }[lesson.distributionScope] : undefined;
  return <footer className="courseware-source-footer">
    <div><span>Source</span>{page.sourceRefs.length > 0 && <SourcePages refs={page.sourceRefs} />}{lesson.sourceTitle && (sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer">{lesson.sourceTitle}</a> : <span>{lesson.sourceTitle}</span>)}</div>
    {(lesson.attributionText || lesson.license || scopeLabel) && <p>{lesson.attributionText}{lesson.license && <span> · License: {lesson.license}</span>}{scopeLabel && <span> · Distribution: {scopeLabel}</span>}</p>}
  </footer>;
}

function safeSourceUrl(value?: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function SourcePages({ refs }: { refs: Array<{ page: number }> }) {
  const pages = [...new Set(refs.map((ref) => ref.page))].sort((left, right) => left - right);
  return <span className="courseware-source-pages">Textbook page{pages.length === 1 ? "" : "s"} {pages.join(", ")}</span>;
}
