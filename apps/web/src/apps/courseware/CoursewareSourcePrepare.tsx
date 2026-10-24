import { useState } from "react";
import { WHOLE_BOOK_LIMITS, type WholeBookStartInput } from "../../api/wholeBook";
import type { LibraryItem } from "../../types/textbook";

export interface CoursewareProfileView {
  subjectId: string;
  label: string;
}

export interface CoursewareSourcePreparationInput {
  autoGenerate: boolean;
  autoPublish: boolean;
  confirmedMaterialKind?: "textbook";
  subject: string;
  title?: string;
  pageStart?: number;
  pageEnd?: number;
  sourceTitle?: string;
  sourceUrl?: string;
  license?: string;
  attributionText?: string;
  distributionScope: "private" | "classroom" | "public";
}

export type WholeBookPreparationInput = WholeBookStartInput;

function normalizePageCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : undefined;
}

export function CoursewareSourcePrepare({ upload, profiles, initialSubject, busy, error, onStart, onStartWholeBook }: {
  upload: LibraryItem;
  profiles: CoursewareProfileView[];
  initialSubject?: string | null;
  busy: boolean;
  error: string;
  onStart: (value: CoursewareSourcePreparationInput) => Promise<void>;
  onStartWholeBook?: (value: WholeBookPreparationInput) => Promise<unknown>;
}) {
  const pageCount = normalizePageCount(upload.pageCount);
  const recommendedScope = pageCount !== undefined && pageCount > 30 && pageCount <= WHOLE_BOOK_LIMITS.maxPages ? "whole_book" : "selected_pages";
  const [productionScope, setProductionScope] = useState<"selected_pages" | "whole_book">(recommendedScope);
  const [materialKind, setMaterialKind] = useState("textbook");
  const [autoGenerate, setAutoGenerate] = useState(true);
  const [subject, setSubject] = useState(() => initialSubject && profiles.some((profile) => profile.subjectId === initialSubject) ? initialSubject : "");
  const [title, setTitle] = useState(upload.chapter || upload.filename.replace(/\.pdf$/i, ""));
  const [sourceTitle, setSourceTitle] = useState(upload.filename.replace(/\.pdf$/i, ""));
  const [sourceUrl, setSourceUrl] = useState("");
  const [license, setLicense] = useState("");
  const [attributionText, setAttributionText] = useState("");
  const [distributionScope, setDistributionScope] = useState<"private" | "classroom" | "public">("private");
  const [pageStart, setPageStart] = useState(pageCount && pageCount <= 30 ? "1" : "");
  const [pageEnd, setPageEnd] = useState(pageCount && pageCount <= 30 ? String(pageCount) : "");
  const [validation, setValidation] = useState("");
  const canStartWholeBook = pageCount !== undefined && pageCount <= WHOLE_BOOK_LIMITS.maxPages;

  const submit = async () => {
    if (upload.materialKind === "paper" || materialKind !== "textbook") {
      setValidation(upload.materialKind === "paper" || materialKind === "paper" ? "试卷请继续使用原题识别与练习工作流。" : "请先确认这份材料是教材。试卷应继续进入原题工作流。 ");
      return;
    }
    if (!profiles.some((profile) => profile.subjectId === subject)) {
      setValidation("请选择服务端已配置的学科，未知学科暂不能生成课件。");
      return;
    }
    if (productionScope === "whole_book") {
      if (!canStartWholeBook) {
        setValidation(`整本自动制作需要先确认 PDF 物理页数，最多支持 ${WHOLE_BOOK_LIMITS.maxPages} 页。`);
        return;
      }
      if (!onStartWholeBook) {
        setValidation("整本自动制作暂时无法启动，请刷新页面后重试。");
        return;
      }
      setValidation("");
      await onStartWholeBook({
        mode: "whole_book",
        ...(upload.materialKind !== "textbook" ? { confirmedMaterialKind: "textbook" as const } : {}),
        subject,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(sourceTitle.trim() ? { sourceTitle: sourceTitle.trim() } : {}),
        ...(sourceUrl.trim() ? { sourceUrl: sourceUrl.trim() } : {}),
        ...(license.trim() ? { license: license.trim() } : {}),
        ...(attributionText.trim() ? { attributionText: attributionText.trim() } : {}),
        distributionScope,
      });
      return;
    }
    const start = pageStart ? Number(pageStart) : undefined;
    const end = pageEnd ? Number(pageEnd) : undefined;
    const outOfBounds = (page: number | undefined) => page !== undefined && (!Number.isInteger(page) || page < 1 || (pageCount !== undefined && page > pageCount));
    const invalidRange = start === undefined || end === undefined
      || outOfBounds(start)
      || outOfBounds(end)
      || (start !== undefined && end !== undefined && (end < start || end - start + 1 > (autoGenerate ? 30 : 80)));
    if (invalidRange) {
      setValidation(`请填写完整的物理页码范围，最多 ${autoGenerate ? 30 : 80} 页，结束页不能早于起始页。`);
      return;
    }
    setValidation("");
    await onStart({
      autoGenerate,
      autoPublish: autoGenerate,
      ...(upload.materialKind !== "textbook" ? { confirmedMaterialKind: "textbook" as const } : {}),
      subject,
      ...(title.trim() ? { title: title.trim() } : {}),
      ...(start !== undefined ? { pageStart: start } : {}),
      ...(end !== undefined ? { pageEnd: end } : {}),
      ...(sourceTitle.trim() ? { sourceTitle: sourceTitle.trim() } : {}),
      ...(sourceUrl.trim() ? { sourceUrl: sourceUrl.trim() } : {}),
      ...(license.trim() ? { license: license.trim() } : {}),
      ...(attributionText.trim() ? { attributionText: attributionText.trim() } : {}),
      distributionScope,
    });
  };

  return <section className="courseware-source-preparation" aria-label="教材制作设置">
    {upload.materialKind === "paper" ? <div className="courseware-source-blocked" role="alert"><strong>这份材料已确认为试卷</strong><p>请返回原题识别与练习工作流；试卷不会进入教材课件流程。</p></div> : <>
      {upload.materialKind !== "textbook" && <label>材料类型
        <select value={materialKind} onChange={(event) => setMaterialKind(event.target.value)}>
          <option value="">请选择材料类型</option>
          <option value="textbook">教材</option>
          <option value="paper">试卷</option>
        </select>
      </label>}
      {materialKind === "paper" && <p className="courseware-source-blocked" role="status">试卷请使用现有原题工作流。选择教材才能继续。</p>}
      <label>学科
        <select value={subject} onChange={(event) => setSubject(event.target.value)} disabled={materialKind !== "textbook" || busy}>
          <option value="">请选择已配置学科</option>
          {profiles.map((profile) => <option key={profile.subjectId} value={profile.subjectId}>{subjectLabel(profile.subjectId, profile.label)}</option>)}
        </select>
      </label>
      {profiles.length === 0 && <p className="courseware-source-blocked" role="status">暂时没有可用学科配置，请完成学科配置后再继续。</p>}
      <fieldset className="courseware-production-scope"><legend>制作范围</legend>
        <label><input type="radio" name="courseware-production-scope" value="selected_pages" checked={productionScope === "selected_pages"} onChange={() => setProductionScope("selected_pages")} disabled={busy} />制作指定页段（每次最多 30 页）</label>
        <label><input type="radio" name="courseware-production-scope" value="whole_book" checked={productionScope === "whole_book"} onChange={() => setProductionScope("whole_book")} disabled={busy || !canStartWholeBook} />整本自动续做{pageCount !== undefined ? `（${pageCount} 页）` : "（需先识别物理页数）"}</label>
        {!canStartWholeBook && pageCount !== undefined && <p role="status">整本自动制作最多支持 {WHOLE_BOOK_LIMITS.maxPages} 页；这份教材有 {pageCount} 页。可按原有流程制作指定页段。</p>}
        <p>整本模式最多 {WHOLE_BOOK_LIMITS.maxPages} 个原 PDF 物理页，按原页码自动分批：每批最多 {WHOLE_BOOK_LIMITS.pagesPerBatch} 个物理页，最多 {WHOLE_BOOK_LIMITS.maxBatches} 批；每批最多制作 {WHOLE_BOOK_LIMITS.maxGeneratedPagesPerBatch} 张课件页。可暂停后继续。</p>
      </fieldset>
      {productionScope === "whole_book" && pageCount !== undefined ? <p className="courseware-whole-book-range">将覆盖原 PDF 第 1–{pageCount} 页；系统按目录边界安排批次，不会静默截断。</p>
        : productionScope === "whole_book" ? <p className="courseware-whole-book-range" role="status">整本自动续做需先识别 PDF 物理页数。</p>
        : <fieldset className="courseware-page-range"><legend>教材物理页码范围（最多 30 页；短教材已选全文）</legend><label>起始页<input type="number" min="1" max={pageCount} value={pageStart} onChange={(event) => setPageStart(event.target.value)} disabled={busy} /></label><label>结束页<input type="number" min="1" max={pageCount} value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} disabled={busy} /></label></fieldset>}
      <details><summary>调整主题与制作方式（可选）</summary>
      <label>课件主题 / 教材单元名称<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} disabled={busy} placeholder="可留空，由教材目录提供单元名称" /></label>
      {productionScope === "selected_pages" && <label className="courseware-source-confirm"><input type="checkbox" checked={autoGenerate} onChange={(event) => setAutoGenerate(event.target.checked)} disabled={busy} />自动完成教程，包括检查、修复和保存</label>}
      {!autoGenerate && <p>已切换为手动制作：只准备教材来源，后续规划、编辑与发布由你控制。</p>}
      </details>
      <details className="courseware-source-license"><summary>来源署名与分发许可</summary>
        <p>默认保存为私有教程，不必先填写对外分发许可。课堂或公开分发时，再登记实际许可和署名。</p>
        <label>来源名称<input value={sourceTitle} maxLength={200} onChange={(event) => setSourceTitle(event.target.value)} disabled={busy} /></label>
        <label>来源链接<input type="url" value={sourceUrl} maxLength={500} onChange={(event) => setSourceUrl(event.target.value)} disabled={busy} placeholder="https://…" /></label>
        <label>许可<input value={license} maxLength={256} onChange={(event) => setLicense(event.target.value)} disabled={busy} placeholder="例如 CC BY 4.0" /></label>
        <label>署名文本<textarea rows={2} value={attributionText} maxLength={500} onChange={(event) => setAttributionText(event.target.value)} disabled={busy} /></label>
        <label>允许的分发范围<select value={distributionScope} onChange={(event) => setDistributionScope(event.target.value as typeof distributionScope)} disabled={busy}><option value="private">仅教师可见</option><option value="classroom">课堂内分发</option><option value="public">公开分发</option></select></label>
      </details>
      {upload.materialKind !== "textbook" && <p className="courseware-source-disclosure">继续生成即按所选“教材”类型制作；试卷请选择原题工作流。</p>}
      {(validation || error) && <p className="courseware-source-error" role="alert">{validation || error}</p>}
      <button type="button" className="is-primary" disabled={busy || !profiles.length || materialKind !== "textbook" || !subject || (productionScope === "whole_book" ? !canStartWholeBook : !pageStart || !pageEnd)} onClick={() => void submit()}>{busy ? "正在提交…" : productionScope === "whole_book" ? "开始整本自动制作" : autoGenerate ? "自动生成教程" : "确认并准备教材来源"}</button>
    </>}
  </section>;
}

function subjectLabel(subjectId: string, fallback: string) {
  if (subjectId === "english") return "英语";
  if (subjectId === "math") return "数学";
  if (subjectId === "science") return "科学";
  return fallback || "未配置学科";
}
