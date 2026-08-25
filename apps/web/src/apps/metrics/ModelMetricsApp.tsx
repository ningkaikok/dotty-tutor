import { useCallback, useEffect, useState } from "react";
import { loadModelCallMetrics, type ModelCallMetricsSnapshot } from "../../api/metrics";
import { toRowViews, type MetricRowView } from "./metricsView";

const DAY_OPTIONS = [7, 14, 30] as const;

/**
 * 模型调用边界指标面板（内容生产端专用，学生端不可见）。
 * 数据来自 GET /api/metrics/model-calls 的只读聚合；本组件不做任何写操作。
 */
export function ModelMetricsApp() {
  const [days, setDays] = useState<number>(7);
  const [snapshot, setSnapshot] = useState<ModelCallMetricsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (windowDays: number) => {
    setLoading(true);
    setError("");
    try {
      setSnapshot(await loadModelCallMetrics(windowDays));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "指标加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(days);
  }, [days, refresh]);

  const rows: MetricRowView[] = snapshot ? toRowViews(snapshot.items) : [];

  return (
    <section className="panel" aria-label="模型调用指标">
      <h2>模型调用指标</h2>
      <p className="muted">
        按 runtime / task / provider / model 分组的调用边界聚合；只读，不包含任何学生数据。
      </p>
      <label>
        统计窗口：
        <select
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
          aria-label="统计窗口天数"
        >
          {DAY_OPTIONS.map((option) => (
            <option key={option} value={option}>
              最近 {option} 天
            </option>
          ))}
        </select>
      </label>
      {loading && <p role="status">加载中…</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {!loading && !error && rows.length === 0 && (
        <p role="status">窗口内没有模型调用记录。</p>
      )}
      {!loading && !error && rows.length > 0 && (
        <table>
          <caption>模型调用边界指标（最近 {snapshot?.days ?? days} 天）</caption>
          <thead>
            <tr>
              <th scope="col">runtime</th>
              <th scope="col">task</th>
              <th scope="col">provider</th>
              <th scope="col">model</th>
              <th scope="col">调用</th>
              <th scope="col">失败</th>
              <th scope="col">失败率</th>
              <th scope="col">平均耗时</th>
              <th scope="col">输出 tokens</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.runtime}/${row.task}/${row.provider}/${row.model}`}>
                <td>{row.runtime}</td>
                <td>{row.task}</td>
                <td>{row.provider}</td>
                <td>{row.model}</td>
                <td>{row.calls}</td>
                <td>{row.failures}</td>
                <td>{row.failureRate === null ? "—" : `${row.failureRate}%`}</td>
                <td>{row.durationLabel}</td>
                <td>{row.tokenLabel}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
