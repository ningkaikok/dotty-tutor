import { useState } from "react";
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

export function CoursewareSourcePrepare({ upload, profiles, busy, error, onStart }: {
  upload: LibraryItem;
  profiles: CoursewareProfileView[];
  busy: boolean;
  error: string;
  onStart: (value: CoursewareSourcePreparationInput) => Promise<void>;
}) {
  const [materialKind, setMaterialKind] = useState("textbook");
  const [autoGenerate, setAutoGenerate] = useState(true);
  const [subject, setSubject] = useState(profiles.length === 1 ? profiles[0].subjectId : "");
  const [title, setTitle] = useState(upload.chapter || upload.filename.replace(/\.pdf$/i, ""));
  const [sourceTitle, setSourceTitle] = useState(upload.filename.replace(/\.pdf$/i, ""));
  const [sourceUrl, setSourceUrl] = useState("");
  const [license, setLicense] = useState("");
  const [attributionText, setAttributionText] = useState("");
  const [distributionScope, setDistributionScope] = useState<"private" | "classroom" | "public">("private");
  const [pageStart, setPageStart] = useState(upload.pageCount && upload.pageCount <= 30 ? "1" : "");
  const [pageEnd, setPageEnd] = useState(upload.pageCount && upload.pageCount <= 30 ? String(upload.pageCount) : "");
  const [validation, setValidation] = useState("");

  const submit = async () => {
    if (upload.materialKind === "paper" || materialKind !== "textbook") {
      setValidation(upload.materialKind === "paper" || materialKind === "paper" ? "试卷请继续使用原题识别与练习工作流。" : "请先确认这份材料是教材。试卷应继续进入原题工作流。 ");
      return;
    }
    if (!profiles.some((profile) => profile.subjectId === subject)) {
      setValidation("请选择服务端已配置的学科，未知学科暂不能生成课件。");
      return;
    }
    const start = pageStart ? Number(pageStart) : undefined;
    const end = pageEnd ? Number(pageEnd) : undefined;
    const outOfBounds = (page: number | undefined) => page !== undefined && (!Number.isInteger(page) || page < 1 || (upload.pageCount !== undefined && page > upload.pageCount));
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

  return <section className="courseware-source-preparation" aria-label="确认教材来源">
    <span className="courseware-eyebrow">SOURCE PREPARATION</span>
    <h2>选择范围，一次生成教程</h2>
    <p>选好学科和页码，剩下交给系统：识别原页、规划、生成、检查和修复，完成后直接打开教程。只有自动处理未解决的问题才需要你介入。</p>
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
          {profiles.map((profile) => <option key={profile.subjectId} value={profile.subjectId}>{profile.label} · {profile.subjectId}</option>)}
        </select>
      </label>
      {profiles.length === 0 && <p className="courseware-source-blocked" role="status">暂时没有可用学科配置，请完成学科配置后再继续。</p>}
      <fieldset className="courseware-page-range"><legend>教材物理页码范围（最多 30 页；短教材已选全文）</legend><label>起始页<input type="number" min="1" max={upload.pageCount ?? undefined} value={pageStart} onChange={(event) => setPageStart(event.target.value)} disabled={busy} /></label><label>结束页<input type="number" min="1" max={upload.pageCount ?? undefined} value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} disabled={busy} /></label></fieldset>
      <details><summary>调整主题与制作方式（可选）</summary>
      <label>课件主题 / 教材单元名称<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} disabled={busy} placeholder="可留空，由教材目录提供单元名称" /></label>
      <label className="courseware-source-confirm"><input type="checkbox" checked={autoGenerate} onChange={(event) => setAutoGenerate(event.target.checked)} disabled={busy} />自动完成教程，包括检查、修复和保存</label>
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
      <button type="button" className="is-primary" disabled={busy || !profiles.length || materialKind !== "textbook" || !subject || !pageStart || !pageEnd} onClick={() => void submit()}>{busy ? "正在提交…" : autoGenerate ? "自动生成教程" : "确认并准备教材来源"}</button>
    </>}
  </section>;
}
