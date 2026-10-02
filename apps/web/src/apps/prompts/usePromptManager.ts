import { useCallback, useRef, useState } from "react";
import { promptRequest, type PromptDetail, type PromptList, type PromptRevision } from "../../api/prompts";

/** 单次操作串行，过期响应不回写到已退出的内容平台会话。 */
export function usePromptManager() {
  const [token, setToken] = useState("");
  const [items, setItems] = useState<PromptDetail[]>([]);
  const [canPublish, setCanPublish] = useState(false);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState("");
  const session = useRef(0);
  const pending = useRef(false);

  const run = useCallback(async (work: () => Promise<void>) => {
    if (pending.current) return;
    const generation = session.current;
    pending.current = true;
    setBusy(true); setError(""); setNotice("");
    try { await work(); }
    catch (failure) { if (generation === session.current) setError(failure instanceof Error ? failure.message : "操作失败"); }
    finally { if (generation === session.current) { pending.current = false; setBusy(false); } }
  }, []);

  const connect = () => run(async () => {
    const generation = session.current;
    const data = await promptRequest<PromptList>(token);
    if (generation !== session.current) return;
    setItems(data.items); setCanPublish(data.canPublish); setConnected(true);
  });
  const replace = (detail: PromptDetail) => setItems((current) => current.map((item) => item.id === detail.id ? detail : item));
  const save = (detail: PromptDetail, text: string, base: string, onSaved: (revision: PromptRevision) => void) => run(async () => {
    const revision = await promptRequest<PromptRevision>(token, `/${encodeURIComponent(detail.id)}/drafts`, { text, baseRevisionId: base });
    replace(await promptRequest<PromptDetail>(token, `/${encodeURIComponent(detail.id)}`));
    onSaved(revision); setPreview(""); setNotice("草稿已保存，尚未生效。请预览后交给内容负责人发布。");
  });
  const render = (detail: PromptDetail, revision: PromptRevision, variables: string) => run(async () => {
    const values: unknown = JSON.parse(variables);
    if (!values || typeof values !== "object" || Array.isArray(values) || Object.values(values).some((value) => typeof value !== "string")) {
      throw new Error("样例变量应为 JSON 对象，每个值填写字符串");
    }
    const data = await promptRequest<{ rendered: string }>(token, `/${encodeURIComponent(detail.id)}/preview`, { revisionId: revision.revisionId, variables: values });
    setPreview(data.rendered); replace(await promptRequest<PromptDetail>(token, `/${encodeURIComponent(detail.id)}`));
    setNotice("变量预览通过。请检查教学内容；预览不会调用模型，也不代表教学质量验证。");
  });
  const activate = (detail: PromptDetail, revision: PromptRevision, action: "publish" | "rollback") => run(async () => {
    replace(await promptRequest<PromptDetail>(token, `/${encodeURIComponent(detail.id)}/activate`, {
      revisionId: revision.revisionId, expectedActiveRevisionId: detail.activeRevisionId, action,
    }));
    setPreview(""); setNotice(action === "rollback" ? "已回滚。新任务使用此版本，正在运行的任务保持原版本。" : "已发布。新任务使用此版本，正在运行的任务保持原版本。");
  });
  const refresh = () => run(async () => {
    const data = await promptRequest<PromptList>(token);
    setItems(data.items); setCanPublish(data.canPublish); setPreview(""); setNotice("已刷新版本列表");
  });
  const disconnect = () => {
    session.current += 1; pending.current = false;
    setToken(""); setItems([]); setConnected(false); setCanPublish(false); setBusy(false); setPreview(""); setError(""); setNotice("");
  };
  return { token, setToken, items, canPublish, connected, busy, error, notice, preview, setPreview, connect, save, render, activate, refresh, disconnect };
}
