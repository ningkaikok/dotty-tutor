import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError } from "../../api/client";
import {
  loadWholeBookForUpload,
  loadWholeBookRun,
  pauseWholeBook,
  resumeWholeBook,
  startWholeBook,
  type WholeBookRunView,
  type WholeBookStartInput,
} from "../../api/wholeBook";

const ACTIVE_STATUSES = new Set(["queued", "running", "waiting_for_source"]);

/** Reads saved progress and exposes only the explicit start and pause/resume commands. */
export function useWholeBookRun(uploadId: string) {
  const [run, setRun] = useState<WholeBookRunView | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const commandIds = useRef(new Map<string, string>());
  const scope = useRef(0);

  const refresh = useCallback(async () => {
    if (!uploadId) return null;
    const requestScope = scope.current;
    const requestUploadId = uploadId;
    setLoading(true);
    try {
      const latest = await loadWholeBookForUpload(requestUploadId);
      if (requestScope !== scope.current) return null;
      setRun(latest);
      setError("");
      return latest;
    } catch (requestError) {
      if (requestScope !== scope.current) return null;
      if (requestError instanceof ApiRequestError && requestError.status === 404) {
        setRun(null);
        setError("");
        return null;
      }
      setError(requestError instanceof Error ? requestError.message : "整本制作进度暂时无法读取。");
      return null;
    } finally {
      if (requestScope === scope.current) setLoading(false);
    }
  }, [uploadId]);

  useEffect(() => {
    scope.current += 1;
    setRun(null);
    setError("");
    setBusy(false);
    if (!uploadId) {
      setLoading(false);
      return () => { scope.current += 1; };
    }
    void refresh();
    return () => { scope.current += 1; };
  }, [uploadId, refresh]);

  useEffect(() => {
    if (!run || run.uploadId !== uploadId || !ACTIVE_STATUSES.has(run.status)) return;
    let active = true;
    const requestScope = scope.current;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await loadWholeBookRun(run.runId);
        if (!active || requestScope !== scope.current) return;
        setRun(next);
        setError("");
        if (ACTIVE_STATUSES.has(next.status)) timer = window.setTimeout(() => void poll(), 1200);
      } catch (requestError) {
        if (!active || requestScope !== scope.current) return;
        setError(requestError instanceof Error ? requestError.message : "整本制作进度暂时无法读取。");
        timer = window.setTimeout(() => void poll(), 2500);
      }
    };
    timer = window.setTimeout(() => void poll(), 900);
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [run, uploadId]);

  const start = async (input: WholeBookStartInput) => {
    if (!uploadId) return;
    const requestScope = scope.current;
    const requestUploadId = uploadId;
    setBusy(true);
    setError("");
    try {
      const next = await startWholeBook(requestUploadId, input);
      if (requestScope !== scope.current) return null;
      setRun(next);
      return next;
    } catch (requestError) {
      if (requestScope !== scope.current) return null;
      setError(requestError instanceof Error ? requestError.message : "整本自动制作未能启动。");
      return null;
    } finally {
      if (requestScope === scope.current) setBusy(false);
    }
  };

  const control = async (action: "pause" | "resume") => {
    if (!run || run.uploadId !== uploadId) return;
    const requestScope = scope.current;
    setBusy(true);
    setError("");
    const commandKey = `${run.runId}:${action}`;
    const commandId = commandIds.current.get(commandKey) ?? crypto.randomUUID();
    commandIds.current.set(commandKey, commandId);
    try {
      const next = action === "pause"
        ? await pauseWholeBook(run.runId, commandId)
        : await resumeWholeBook(run.runId, commandId);
      if (requestScope !== scope.current) return;
      commandIds.current.delete(commandKey);
      setRun(next);
    } catch (requestError) {
      if (requestScope !== scope.current) return;
      setError(requestError instanceof Error ? requestError.message : "整本制作控制未能保存，请重试。");
    } finally {
      if (requestScope === scope.current) setBusy(false);
    }
  };

  return { run, loading, busy, error, start, pause: () => control("pause"), resume: () => control("resume"), refresh };
}
