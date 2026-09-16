import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { loadDependencyPreflight, type DependencyPreflightReport } from "../../api/dependencyPreflight";
import "./metrics.css";

/**
 * 环境依赖自检页（内容生产端专用，学生端不可见）。
 * 只读探测 MinerU/pypdf/Ollama/Codex CLI/Azure Speech/Qwen3-TTS/PostgreSQL 是否配置就绪，
 * 回答"现在这条链路能不能跑"，不涉及任何学生数据。
 */
export function DependencyPreflightApp() {
  const navigate = useNavigate();
  const [report, setReport] = useState<DependencyPreflightReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await loadDependencyPreflight());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "自检报告加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="metrics-shell">
      <header className="import-header">
        <button className="route-back-button" onClick={() => navigate("/studio")}>← 返回工作台</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>环境依赖自检</span>
        </div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="panel metrics-panel" aria-label="环境依赖自检报告">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">CONTENT STUDIO · 只读诊断</span>
            <h2>环境依赖自检</h2>
            <p className="muted">
              一次性检查这台机器上的 OCR、模型和数据库依赖是否配置就绪；只读探测，不修改任何配置。
            </p>
          </div>
          <button type="button" disabled={loading} onClick={() => void refresh()}>
            {loading ? "检查中…" : "重新检查"}
          </button>
        </div>

        <div className="metrics-body">
          {loading && !report && <p role="status" className="muted">正在检查依赖…</p>}
          {error && <p role="alert" className="error-text">{error}</p>}
          {report && (
            <>
              <div
                className={`preflight-overall ${report.ok ? "preflight-overall-ok" : "preflight-overall-fail"}`}
                role="status"
              >
                {report.ok ? "必需依赖全部就绪" : "有必需依赖未就绪，对应功能会失败"}
              </div>

              <ul className="preflight-list" aria-label="逐项依赖检查">
                {report.checks.map((check) => (
                  <li
                    key={check.key}
                    className={`preflight-item ${check.ok ? "preflight-item-ok" : "preflight-item-fail"}`}
                  >
                    <span className="preflight-item-status" aria-hidden="true">{check.ok ? "✓" : "✗"}</span>
                    <div className="preflight-item-body">
                      <div className="preflight-item-heading">
                        <strong>{check.label}</strong>
                        <span className={`preflight-item-badge ${check.optional ? "preflight-item-optional" : "preflight-item-required"}`}>
                          {check.optional ? "可选" : "必需"}
                        </span>
                      </div>
                      <p className="muted">{check.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>
    </main>
  );
}
