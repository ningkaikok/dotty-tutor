import { useState } from "react";

/** Confirm removal in place; a failed request keeps the material visible and retryable. */
export function MaterialDeleteAction({ name, onDelete }: { name: string; onDelete: () => Promise<void> }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    setBusy(true);
    setError("");
    try { await onDelete(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败，请重试"); setBusy(false); }
  }
  return <div className="material-delete">
    {confirming ? <><p>从列表移除“{name}”？原文件与历史记录会保留。</p><div><button type="button" disabled={busy} onClick={() => void remove()}>{busy ? "正在删除…" : "确认删除"}</button><button type="button" disabled={busy} onClick={() => { setConfirming(false); setError(""); }}>取消</button></div></> : <button type="button" aria-label={`删除 ${name}`} onClick={() => setConfirming(true)}>删除</button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
