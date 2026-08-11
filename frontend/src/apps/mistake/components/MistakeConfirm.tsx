import { useState } from "react";
import { confirmMistake } from "../../../api";
import MathText from "../../../MathText";
import type { MistakeConfirmation, MistakeErrorReason, MistakeItem } from "../../../types";

interface MistakeConfirmProps {
  item: MistakeItem;
  onSaved: (item: MistakeItem) => void;
}

const ERROR_REASONS: Array<[MistakeErrorReason, string, string]> = [
  ["concept", "概念不清", "定义、公式或原理没有理解"],
  ["reading", "审题错误", "遗漏或误解了题目条件"],
  ["calculation", "计算失误", "方法正确但运算出错"],
  ["missing_step", "步骤遗漏", "推导、证明或单位不完整"],
  ["unknown", "完全不会", "不知道从哪里开始"],
  ["careless", "粗心大意", "会做但抄错、看错或没检查"],
];

export function MistakeConfirm({ item, onSaved }: MistakeConfirmProps) {
  const question = item.questionPayload.question;
  const fromPublishedPaper = item.contentType === "application/vnd.dotty.publication+json";
  const [form, setForm] = useState<Omit<MistakeConfirmation, "errorReason"> & { errorReason: MistakeErrorReason | "" }>({
    prompt: question.prompt,
    originalAnswer: item.originalAnswer,
    subject: item.subject || "数学",
    gradeBand: item.gradeBand || "初中",
    chapter: item.chapter,
    knowledgePoint: item.knowledgePoint,
    errorReason: item.errorReason ?? "",
    notes: item.notes,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const update = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const save = async () => {
    if (saving) return;
    if (!form.prompt.trim() || !form.chapter.trim() || !form.knowledgePoint.trim()) {
      setError("题干、章节和知识点不能为空");
      return;
    }
    if (!form.errorReason) {
      setError("请选择这次做错的主要原因");
      return;
    }
    setSaving(true);
    setError("");
    try {
      onSaved(await confirmMistake(item.mistakeId, { ...form, errorReason: form.errorReason }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "确认保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mistake-confirm-page">
      <div className="mistake-section-heading">
        <span className="eyebrow">STEP 02 · CONFIRM</span>
        <h1>确认题目与错误原因</h1>
        <p>{fromPublishedPaper
          ? "题目来自已发布互动试卷，可在这里补充错误原因和订正备注。"
          : "AI 识别可能出错。请以原图为准修正题干和归类，再保存到错题本。"}</p>
      </div>

      <section className="mistake-confirm-grid">
        <aside className="mistake-source-card">
          {item.sourceImageUrl ? (
            <img src={item.sourceImageUrl} alt="上传的错题原图" />
          ) : (
            <div className="mistake-published-source">
              <span>互动试卷自动记录</span>
              <strong>{item.sourceFilename}</strong>
            </div>
          )}
          <div>
            <span>识别结果预览</span>
            <MathText text={form.prompt} block />
            {question.options?.length ? (
              <ol>{question.options.map((option) => <li key={option}><MathText text={option} /></li>)}</ol>
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
          <div className="classification-grid">
            <label><span>学段</span><select value={form.gradeBand} onChange={(event) => update("gradeBand", event.target.value)}><option>小学</option><option>初中</option><option>普高</option><option>中职</option></select></label>
            <label><span>学科</span><input value={form.subject} onChange={(event) => update("subject", event.target.value)} /></label>
            <label><span>教材章节</span><input value={form.chapter} onChange={(event) => update("chapter", event.target.value)} /></label>
            <label><span>知识点</span><input value={form.knowledgePoint} onChange={(event) => update("knowledgePoint", event.target.value)} /></label>
          </div>

          <fieldset className="error-reason-fieldset">
            <legend>这次为什么做错？</legend>
            <p>错误原因会决定后续出题和提示策略。</p>
            <div>
              {ERROR_REASONS.map(([value, label, description]) => (
                <label key={value} className={form.errorReason === value ? "selected" : ""}>
                  <input
                    type="radio"
                    name="errorReason"
                    value={value}
                    checked={form.errorReason === value}
                    onChange={() => update("errorReason", value)}
                  />
                  <span><strong>{label}</strong><small>{description}</small></span>
                </label>
              ))}
            </div>
          </fieldset>

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
