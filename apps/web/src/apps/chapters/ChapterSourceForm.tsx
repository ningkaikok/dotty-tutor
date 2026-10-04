import { useState, type FormEvent } from "react";
import type { LibraryItem } from "../../types/textbook";
import type { ChapterSource, ChapterSourceFlag, ChapterSubject } from "../../types/chapter";

interface ChapterSourceFormProps {
  libraries: LibraryItem[];
  initialTitle?: string;
  initialUploadId?: string;
  initialSubject?: ChapterSubject;
  busy?: boolean;
  submitLabel: string;
  onSubmit: (value: { title: string; subject: ChapterSubject; source: ChapterSource }) => void;
}

const sourceFlags: Array<[ChapterSourceFlag, string]> = [
  ["missing_conditions", "可能缺少条件"],
  ["wrong_figure", "图文归属或图片可能错误"],
  ["unreadable", "页面难以辨认"],
];

/** Sources are always tied to explicit pages; pasted OCR is accepted as a single, numbered page. */
export function ChapterSourceForm({
  libraries,
  initialTitle = "",
  initialUploadId = "",
  initialSubject = "math",
  busy = false,
  submitLabel,
  onSubmit,
}: ChapterSourceFormProps) {
  const selected = libraries.find((item) => item.uploadId === initialUploadId);
  const [title, setTitle] = useState(initialTitle || selected?.filename.replace(/\.pdf$/i, "") || "");
  const [subject, setSubject] = useState<ChapterSubject>(initialSubject);
  const [uploadId, setUploadId] = useState(selected?.uploadId ?? "");
  const [pageStart, setPageStart] = useState("1");
  const [pageEnd, setPageEnd] = useState("1");
  const [sourceVersion, setSourceVersion] = useState("1");
  const [license, setLicense] = useState("");
  const [flagsPage, setFlagsPage] = useState("");
  const [ocrText, setOcrText] = useState("");
  const [flags, setFlags] = useState<ChapterSourceFlag[]>([]);
  const [regionEnabled, setRegionEnabled] = useState(false);
  const [region, setRegion] = useState({ x: "0.1", y: "0.1", width: "0.8", height: "0.25" });
  const [validationError, setValidationError] = useState("");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const start = Number(pageStart);
    const end = Number(pageEnd);
    if (!title.trim()) return setValidationError("请填写课程名称");
    if (!license.trim()) return setValidationError("请填写教材来源许可或待核实说明");
    if (!Number.isInteger(start) || start < 1 || !Number.isInteger(end) || end < start) {
      return setValidationError("请填写有效的起止页码");
    }
    if (end - start + 1 > 80) return setValidationError("一次最多选择 80 页教材来源");
    if (!uploadId && (!ocrText.trim() || start !== end)) {
      return setValidationError("粘贴原文时请填写一个页码；跨页内容请选择教材库中的已识别教材");
    }
    const x = Number(region.x);
    const y = Number(region.y);
    const width = Number(region.width);
    const height = Number(region.height);
    if (regionEnabled && (!Number.isFinite(x + y + width + height) || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1 || y + height > 1)) {
      return setValidationError("区域坐标必须位于页面范围内，且宽高大于 0");
    }
    const issuePage = flagsPage ? Number(flagsPage) : start;
    if (uploadId && flags.length && (!Number.isInteger(issuePage) || issuePage < start || issuePage > end)) {
      return setValidationError("问题标记页必须位于所选教材页范围内");
    }

    setValidationError("");
    const source: ChapterSource = uploadId
      ? {
        uploadId,
        sourceVersion: sourceVersion.trim() || "1",
        license: license.trim(),
        pageStart: start,
        pageEnd: end,
        pageFlags: flags.length ? [{ page: issuePage, flags }] : [],
      }
      : {
        sourceVersion: sourceVersion.trim() || "1",
        ...(license.trim() ? { license: license.trim() } : {}),
        pageStart: start,
        pageEnd: end,
        pages: [{
          page: start,
          text: ocrText.trim(),
          regions: regionEnabled ? [{
            regionId: `manual-p${start}-r1`,
            x, y, width, height,
          }] : [],
          flags,
        }],
      };
    onSubmit({ title: title.trim(), subject, source });
  };

  return (
    <form className="chapter-source-form" onSubmit={submit} aria-label="章节来源">
      <label>
        课程名称
        <input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} />
      </label>
      <label>
        学科
        <select value={subject} onChange={(event) => setSubject(event.target.value as ChapterSubject)}>
          <option value="math">数学</option>
          <option value="english">英语阅读</option>
        </select>
      </label>
      <label>
        教材来源
        <select value={uploadId} onChange={(event) => {
          const item = libraries.find((entry) => entry.uploadId === event.target.value);
          setUploadId(event.target.value);
          setPageStart("1"); setPageEnd("1");
          if (!title.trim() && item) setTitle(item.filename.replace(/\.pdf$/i, ""));
        }}>
          <option value="">粘贴教材原文</option>
          {libraries.map((item) => (
            <option value={item.uploadId} key={item.uploadId}>
              {item.filename}（{item.pageCount ?? "页数未知"} 页）
            </option>
          ))}
        </select>
      </label>
      <p className="chapter-help">先选一页开始，需要跨页时再调整范围。</p>
      <div className="chapter-page-range">
        <label>起始页<input type="number" min="1" max={pageEnd ? Number(pageEnd) + 79 : undefined} value={pageStart} onChange={(event) => { if (pageEnd === pageStart) setPageEnd(event.target.value); setPageStart(event.target.value); }} required /></label>
        <label>结束页<input type="number" min="1" max={pageStart ? Number(pageStart) + 79 : undefined} value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} required /></label>
      </div>
      <label>来源许可说明<input value={license} onChange={(event) => setLicense(event.target.value)} maxLength={256} placeholder="例如：校内授权 / CC BY 4.0 / 待核实" required /></label>
      {!uploadId && (
        <label>
          OCR 原文
          <textarea value={ocrText} onChange={(event) => setOcrText(event.target.value)} rows={5} placeholder="粘贴一页已识别的教材原文" />
        </label>
      )}
      <details className="chapter-advanced"><summary>高级来源设置（可选）</summary><div>
      <label>
        来源版本
        <input value={sourceVersion} onChange={(event) => setSourceVersion(event.target.value)} maxLength={128} />
      </label>
      <fieldset className="chapter-source-flags">
        <legend>来源问题标记（标记后会阻止发布）</legend>
        {uploadId && <label>问题标记页<input aria-label="问题标记页" type="number" min={pageStart || 1} max={pageEnd || undefined} value={flagsPage} onChange={(event) => setFlagsPage(event.target.value)} placeholder={`默认第 ${pageStart || "起始"} 页`} /></label>}
        {sourceFlags.map(([flag, label]) => (
          <label key={flag}><input type="checkbox" checked={flags.includes(flag)} onChange={() => setFlags((current) => current.includes(flag) ? current.filter((item) => item !== flag) : [...current, flag])} />{label}</label>
        ))}
      </fieldset>
      {!uploadId && <fieldset className="chapter-manual-region">
        <legend>OCR 原文区域（可选）</legend>
        <label><input type="checkbox" checked={regionEnabled} onChange={(event) => setRegionEnabled(event.target.checked)} />标记原文所在区域</label>
        {regionEnabled && <div>{(["x", "y", "width", "height"] as const).map((key) => <label key={key}>{key}<input type="number" min={key === "width" || key === "height" ? "0.01" : "0"} max="1" step="0.01" value={region[key]} onChange={(event) => setRegion((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>}
      </fieldset>}
      </div></details>
      {validationError && <p role="alert">{validationError}</p>}
      <button type="submit" disabled={busy}>{busy ? "正在保存…" : submitLabel}</button>
    </form>
  );
}
