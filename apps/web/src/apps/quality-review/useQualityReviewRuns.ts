import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelQualityReviewRun,
  createQualityReviewRun,
  getQualityReviewGoldExport,
  getQualityReviewRun,
  getQualityReviewRunStatistics,
  listQualityReviewRunModels,
  listQualityReviewRuns,
  rateQualityReviewCase,
  type QualityReviewModel,
  type QualityReviewRun,
  type QualityReviewRunCreate,
} from "../../api/qualityReviewRuns";

export function useQualityReviewRuns() {
  const [models, setModels] = useState<QualityReviewModel[]>([]);
  const [run, setRun] = useState<QualityReviewRun | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [statistics, setStatistics] = useState<Record<string, unknown> | null>(null);
  const [goldExport, setGoldExport] = useState<Record<string, unknown> | null>(null);
  const runRequestGeneration = useRef(0);
  const currentRun = useRef<QualityReviewRun | null>(null);

  const publishRun = useCallback((next: QualityReviewRun | null) => {
    currentRun.current = next;
    setRun(next);
  }, []);

  const refresh = useCallback(async () => {
    const requestGeneration = ++runRequestGeneration.current;
    setError("");
    try {
      const [modelItems, runs] = await Promise.all([listQualityReviewRunModels(), listQualityReviewRuns()]);
      if (requestGeneration !== runRequestGeneration.current) return;
      setModels(modelItems);
      publishRun(runs[0] ?? null);
    } catch (cause) {
      if (requestGeneration === runRequestGeneration.current) {
        setError(cause instanceof Error ? cause.message : "读取模型对比记录失败");
      }
    }
  }, [publishRun]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return undefined;
    const timer = window.setTimeout(() => {
      const runId = run.runId;
      const requestGeneration = ++runRequestGeneration.current;
      void getQualityReviewRun(runId).then((latest) => {
        if (requestGeneration !== runRequestGeneration.current || currentRun.current?.runId !== runId) return;
        publishRun(latest);
      }).catch((cause: unknown) => {
        if (requestGeneration === runRequestGeneration.current && currentRun.current?.runId === runId) {
          setError(cause instanceof Error ? cause.message : "刷新模型运行状态失败");
        }
      });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [run, publishRun]);

  const start = useCallback(async (input: QualityReviewRunCreate) => {
    const requestGeneration = ++runRequestGeneration.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await createQualityReviewRun(input);
      if (requestGeneration !== runRequestGeneration.current) return;
      publishRun(created);
      setStatistics(null);
      setNotice("运行已持久保存。可刷新页面恢复进度；每次只允许一个有界运行。");
    } catch (cause) {
      if (requestGeneration === runRequestGeneration.current) {
        setError(cause instanceof Error ? cause.message : "启动模型对比失败");
      }
    } finally { setBusy(false); }
  }, [publishRun]);

  const cancel = useCallback(async () => {
    if (!run) return;
    const runId = run.runId;
    const requestGeneration = ++runRequestGeneration.current;
    setBusy(true);
    try {
      const cancelled = await cancelQualityReviewRun(runId);
      if (requestGeneration !== runRequestGeneration.current || currentRun.current?.runId !== runId) return;
      publishRun(cancelled);
      setNotice("已请求取消。正在执行的模型调用会在真实传输超时内结束，未完成样本不会排名。");
    } catch (cause) {
      if (requestGeneration === runRequestGeneration.current && currentRun.current?.runId === runId) {
        setError(cause instanceof Error ? cause.message : "取消模型运行失败");
      }
    } finally { setBusy(false); }
  }, [publishRun, run]);

  const rate = useCallback(async (input: {
    caseId: string;
    aPass: boolean;
    bPass: boolean;
    preferred: "A" | "B" | "tie" | "neither";
    rationale: string;
  }) => {
    if (!run) return;
    const runId = run.runId;
    const requestGeneration = ++runRequestGeneration.current;
    setBusy(true);
    setError("");
    try {
      const rated = await rateQualityReviewCase(runId, input);
      if (requestGeneration !== runRequestGeneration.current || currentRun.current?.runId !== runId) return;
      publishRun(rated);
      setNotice("评分已保存到数据库，并与本次盲化案例绑定。");
    } catch (cause) {
      if (requestGeneration === runRequestGeneration.current && currentRun.current?.runId === runId) {
        setError(cause instanceof Error ? cause.message : "保存评分失败");
      }
    } finally { setBusy(false); }
  }, [publishRun, run]);

  const loadStatistics = useCallback(async () => {
    if (!run) return;
    try { setStatistics(await getQualityReviewRunStatistics(run.runId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "读取统计结果失败"); }
  }, [run]);

  const loadGoldExport = useCallback(async () => {
    try { setGoldExport(await getQualityReviewGoldExport()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "读取金标准导出状态失败"); }
  }, []);

  return {
    models,
    run,
    error,
    notice,
    busy,
    statistics,
    goldExport,
    refresh,
    start,
    cancel,
    rate,
    loadStatistics,
    loadGoldExport,
  };
}
