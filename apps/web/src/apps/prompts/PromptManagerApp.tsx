import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { PromptDetail, PromptRevision } from "../../api/prompts";
import { usePromptManager } from "./usePromptManager";
import "./prompts.css";

const LABELS: Record<string, string> = {
  "generation.extraction": "原题抽取", "generation.solution": "独立求解", "generation.verification": "答案核验",
  "generation.tutor-script": "教学脚本", "tutor.stable": "陪练教学规则", "tutor.dynamic": "陪练本轮上下文", "evaluation.judge": "评测评分标准",
};
const SAMPLES: Record<string, string> = {
  source_block_ids: '["sample-block-1"]', visual_asset_ids: "[]", source: "1. 已知 x + 2 = 5，求 x。", repair: "",
  question_ir: JSON.stringify({ stem: "已知 x + 2 = 5，求 x。", givens: ["x + 2 = 5"] }),
  question: "已知 x + 2 = 5，求 x。", givens: "x + 2 = 5", lesson_steps: '[{"text":"等式两边同时减去 2"}]',
  hint_level: "0", guide_card: '{"hint":"想想怎样消去左边的常数"}', student_input: "两边同时减去 2", interaction_result: "无",
  formula_recognitions: "无", canvas_state: "无", conversation_context: "这是本线程第一轮", mode: "请求下一步提示",
  conflict_instruction: "未发现同左边等式冲突，仍需自行核对。", rubric: "清晰度、针对性、事实性各 1–5 分", question_context: "x + 2 = 5", explanation: "等式两边同时减去 2。",
};

export function PromptManagerApp() {
  const manager = usePromptManager();
  const [params] = useSearchParams();
  const [selectedId, setSelectedId] = useState(params.get("template") || "generation.extraction");
  const [selectedRevisionId, setSelectedRevisionId] = useState("");
  const [text, setText] = useState("");
  const [variables, setVariables] = useState("{}");
  const detail = manager.items.find((item) => item.id === selectedId);
  const deepHash = params.get("hash");
  const revision = detail?.revisions.find((item) => item.revisionId === selectedRevisionId)
    || (deepHash ? detail?.revisions.find((item) => item.contentHash === deepHash && (!params.get("version") || item.version === params.get("version"))) : undefined)
    || detail?.revisions.find((item) => item.revisionId === detail.activeRevisionId);
  const active = detail?.revisions.find((item) => item.revisionId === detail.activeRevisionId);
  const dirty = revision ? text !== revision.text : false;
  const historicalMissing = Boolean(deepHash && !selectedRevisionId && detail && params.get("template") === detail.id && !detail.revisions.some((item) => item.contentHash === deepHash && (!params.get("version") || item.version === params.get("version"))));

  const variableKeys = (detail?.variables || []).join(",");

  useEffect(() => { setText(revision?.text || ""); }, [revision?.revisionId, revision?.text]);
  useEffect(() => {
    setVariables(JSON.stringify(Object.fromEntries(variableKeys.split(",").filter(Boolean).map((key) => [key, SAMPLES[key] ?? "样例"])), null, 2));
  }, [detail?.id, variableKeys]);

  const choose = (item: PromptDetail, version?: PromptRevision) => {
    setSelectedId(item.id); setSelectedRevisionId(version?.revisionId || item.activeRevisionId); manager.setPreview("");
  };
  return <main className="app-shell prompt-shell">
    <header className="topbar prompt-header">
      <Link className="route-back-button" to="/studio">← 内容生产工作台</Link>
      <div className="brand-mark" aria-hidden="true">D</div>
      <div className="prompt-brand"><strong>Dotty</strong><span>内容平台 · 教学策略管理</span></div>
      <span className="status">{manager.connected ? manager.canPublish ? "内容发布权限" : "内容编辑权限" : "内容管理入口"}</span>
      {manager.connected && <div className="prompt-actions"><button disabled={manager.busy || dirty} onClick={() => void manager.refresh()}>刷新版本</button><button disabled={manager.busy} onClick={manager.disconnect}>退出管理</button></div>}
    </header>
    <section className="prompt-intro">
      <span className="eyebrow">教学策略</span>
      <h1>教学策略 · 提示词管理</h1>
      <p className="muted">维护教学规则，预览和比较版本，让每一次内容调整都有据可查。</p>
    </section>
    {!manager.connected ? <form className="prompt-login panel" onSubmit={(event) => { event.preventDefault(); void manager.connect(); }}>
      <span className="eyebrow">内容平台访问</span>
      <h2>进入内容平台管理</h2>
      <p className="muted">供内容老师、教研和内容负责人维护教学策略。授课老师从教师工作台使用已发布能力。</p>
      <div className="prompt-access-note" id="prompt-credential-help">
        <strong>凭据从哪里获取？</strong>
        <p>请向项目维护者获取内容管理访问令牌。编辑凭据可保存和预览草稿；发布凭据还可发布与回滚版本。</p>
      </div>
      <label>内容平台凭据<input type="password" autoComplete="off" aria-describedby="prompt-credential-help prompt-credential-privacy" placeholder="输入维护者提供的访问令牌" disabled={manager.busy} value={manager.token} onChange={(event) => manager.setToken(event.target.value)} /></label>
      <p className="muted" id="prompt-credential-privacy">凭据仅保留在当前页面内存中，刷新页面或退出管理后需重新输入。</p>
      <button className="prompt-primary" disabled={manager.busy || !manager.token.trim()}>{manager.busy ? "正在验证…" : "进入管理"}</button>
    </form> : <div className="prompt-layout">
      <nav aria-label="教学提示词">{manager.items.map((item) => <button key={item.id} disabled={manager.busy || dirty} aria-current={item.id === selectedId ? "page" : undefined} onClick={() => choose(item)}>{LABELS[item.id] || item.id}</button>)}</nav>
      {detail && revision && active && <section className="prompt-editor panel">
        <h2>{LABELS[detail.id] || detail.id}</h2><p>当前生效：{active.version}。保存草稿不会影响学生；发布后的新任务才使用新版本。</p>
        {historicalMissing && <p role="alert">记录中的历史版本尚未归档，无法定位原文。下方为当前版本，请勿把它当作当时使用的版本。</p>}
        {!detail.editable && <p>评测评分标准由开发维护者管理，内容平台只读。</p>}
        <label>查看版本<select aria-label="查看版本" disabled={manager.busy || dirty} value={revision.revisionId} onChange={(event) => {
          const next = detail.revisions.find((item) => item.revisionId === event.target.value); if (next) choose(detail, next);
        }}>{detail.revisions.map((item) => <option key={item.revisionId} value={item.revisionId}>{item.version} · {item.revisionId === detail.activeRevisionId ? "生效中" : item.published ? "曾发布" : "草稿"}</option>)}</select></label>
        <small>内容摘要：{revision.contentHash}</small>
        <label>提示词正文<textarea aria-label="提示词正文" rows={15} value={text} readOnly={!detail.editable} disabled={manager.busy} onChange={(event) => { setText(event.target.value); manager.setPreview(""); }} /></label>
        <p>保留变量：{detail.variables.map((name) => `\${${name}}`).join("、")}</p>
        <div className="prompt-actions">{dirty && <button disabled={manager.busy} onClick={() => setText(revision.text)}>放弃未保存修改</button>}<button className="prompt-primary" disabled={manager.busy || !detail.editable || !dirty} onClick={() => void manager.save(detail, text, revision.revisionId, (saved) => setSelectedRevisionId(saved.revisionId))}>保存为新草稿</button>
          <button disabled={manager.busy || dirty} onClick={() => void manager.render(detail, revision, variables)}>预览已保存版本</button>
          {manager.canPublish && detail.editable && <button disabled={manager.busy || dirty || revision.revisionId === detail.activeRevisionId || (!revision.published && !revision.previewed)} onClick={() => void manager.activate(detail, revision, revision.published ? "rollback" : "publish")}>{revision.published ? "回滚到此版本" : "发布此草稿"}</button>}
        </div>
        <details><summary>与当前生效版本比较</summary><div className="prompt-compare"><div><h3>当前生效</h3><pre>{active.text}</pre></div><div><h3>当前编辑</h3><pre>{text}</pre></div></div></details>
        <label>样例变量<textarea aria-label="样例变量" rows={6} value={variables} disabled={manager.busy} onChange={(event) => setVariables(event.target.value)} /></label>
        <p>默认样例为合成数据，可替换为脱敏样例。变量预览不调用模型，不代表教学效果验证。</p>
        {manager.preview && <section aria-label="渲染预览"><h3>渲染预览</h3><pre>{manager.preview}</pre></section>}
        <details><summary>发布记录（{detail.events.length}）</summary>{detail.events.map((event, index) => <p key={index}>{event.action === "rollback" ? "回滚" : "发布"} · {new Date(event.createdAt * 1000).toLocaleString()} · {event.createdBy} · {event.revisionId}</p>)}</details>
      </section>}
    </div>}
    {manager.error && <p className="prompt-error" role="alert">{manager.error}</p>}
    {manager.notice && <p role="status">{manager.notice}</p>}
  </main>;
}
