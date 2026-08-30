import { useState } from "react";
import { confirmMistake } from "../../../api/mistakes";
import { RichText } from "../../../RichText";
import type { MistakeConfirmation, MistakeItem } from "../../../types/index";
import { displayedPrompt, optionText } from "../../../questionPresentation";

interface MistakeConfirmProps {
  item: MistakeItem;
  onSaved: (item: MistakeItem) => void;
}

export function MistakeConfirm({ item, onSaved }: MistakeConfirmProps) {
  const question = item.questionPayload.question;
  const fromPublishedPaper = item.contentType === "application/vnd.dotty.publication+json";
  const [form, setForm] = useState<MistakeConfirmation>({
    // 旧错题可能把图片 Markdown 持久化在 prompt；编辑页应展示可读文本，保存后
    // 由后端继续以结构化图片字段作为权威来源，避免旧字符串再次污染学生端。
    prompt: displayedPrompt(question),
    originalAnswer: item.originalAnswer,
    subject: item.subject || "数学",
    gradeBand: item.gradeBand || "初中",
    chapter: item.chapter,
    knowledgePoint: item.knowledgePoint,
    notes: item.notes,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [sourceImageBroken, setSourceImageBroken] = useState(false);

  const update = <K extends keyof MistakeConfirmation>(key: K, value: MistakeConfirmation[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const save = async () => {
    if (saving) return;
    if (!form.prompt.trim()) {
      setError("题干不能为空");
      return;
    }
    setSaving(true);
    setError("");
    try {
      onSaved(await confirmMistake(item.mistakeId, form));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "确认保存失败");
    } finally {
      setSaving(false);
    }
  };

  const classificationSummary = `分类：${form.gradeBand} · ${form.subject} · ${form.chapter || "未分类章节"} · ${form.knowledgePoint || "未分类知识点"}`;

  return (
    <section className="mistake-confirm-page">
      <div className="mistake-section-heading">
        <span className="eyebrow">第 2 步 · 确认</span>
        <h1>确认题目</h1>
        <p>{fromPublishedPaper
          ? "题目来自已发布互动试卷，确认题干无误即可保存；稍后陪练时会先问你觉得错在哪。"
          : "AI 识别可能出错。请以原图为准修正题干，再保存到错题本；稍后陪练时会先问你觉得错在哪，这一步不用现在填。"}</p>
      </div>

      <section className="mistake-confirm-grid">
        <aside className="mistake-source-card">
          {item.sourceImageUrl && !sourceImageBroken ? (
            <img src={item.sourceImageUrl} alt="上传的错题原图" onError={() => setSourceImageBroken(true)} />
          ) : (
            <div className="mistake-published-source">
              <span>{fromPublishedPaper ? "互动试卷自动记录" : "原图暂不可用"}</span>
              <strong>{fromPublishedPaper ? item.sourceFilename : "请重新上传或检查数据目录"}</strong>
            </div>
          )}
          <div>
            <span>识别结果预览</span>
            <RichText text={form.prompt} />
            {question.options?.length ? (
              <ol>{question.options.map((option) => <li key={option}><RichText text={optionText(option)} /></li>)}</ol>
            ) : null}
          </div>
          <small>{fromPublishedPaper ? "来源：已发布互动试卷" : `OCR：${item.ocrRun.provider} · 模型：${item.modelRun.provider}`}</small>
        </aside>

        <div className="mistake-confirm-form">
          <label className="full-field">
            <span>题干与公式</span>
            <textarea value={form.prompt} onChange={(event) => update("prompt", event.target.value)} />
            <small>公式可保留 `$...$` 或 `$$...$$` LaTeX 格式。</small>
          </label>
          <label className="full-field">
            <span>我当时的答案或步骤</span>
            <textarea
              value={form.originalAnswer}
              onChange={(event) => update("originalAnswer", event.target.value)}
              placeholder="没有记录时可以留空"
            />
          </label>

          <details className="collapse-drawer panel">
            <summary className="collapse-drawer-summary">
              <span>{classificationSummary}</span>
              <i className="drawer-caret" aria-hidden="true" />
            </summary>
            <div className="classification-grid">
              <label><span>学段</span><select value={form.gradeBand} onChange={(event) => update("gradeBand", event.target.value)}><option>小学</option><option>初中</option><option>普高</option><option>中职</option></select></label>
              <label><span>学科</span><input value={form.subject} onChange={(event) => update("subject", event.target.value)} /></label>
              <label><span>教材章节</span><input value={form.chapter} onChange={(event) => update("chapter", event.target.value)} /></label>
              <label><span>知识点</span><input value={form.knowledgePoint} onChange={(event) => update("knowledgePoint", event.target.value)} /></label>
            </div>
          </details>

          <label className="full-field">
            <span>补充备注 <small>可选</small></span>
            <textarea value={form.notes} onChange={(event) => update("notes", event.target.value)} placeholder="例如：移项时忘记变号" />
          </label>
          {error && <p className="mistake-error" role="alert">{error}</p>}
          <button className="mistake-primary-action" disabled={saving} onClick={() => void save()}>
            {saving ? "正在保存…" : "确认并保存到错题本"}
          </button>
        </div>
      </section>
    </section>
  );
}
