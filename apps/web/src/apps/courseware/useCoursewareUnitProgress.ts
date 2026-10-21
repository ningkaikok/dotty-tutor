import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError } from "../../api/client";
import { loadCoursewareUnit, saveCoursewarePosition, type CoursewareUnit } from "../../api/coursewareUnit";
import { useLearnerId } from "../../api/identity";

/** 尚未被服务端确认的一次写入；重试时必须原样复用这三个值。 */
type PendingWrite = { commandId: string; pageId: string; expectedVersion: number };

type WriteQueue = {
  scope: string;
  /** 目录加载完成前不允许写入：版本号还没读到，先写只会撞 409。 */
  loaded: boolean;
  version: number;
  /** 学习者当前停留的页面，可能比已确认页面更新。 */
  targetPageId: string;
  /** 服务端最后确认过的页面；与 target 相同则无需写入。 */
  confirmedPageId: string;
  /** 结果未知或尚未确认的写入；网络失败后保留，下次翻页沿用同一 commandId。 */
  pending: PendingWrite | null;
  running: boolean;
  blocked: boolean;
};

function newQueue(scope: string): WriteQueue {
  return { scope, loaded: false, version: 0, targetPageId: "", confirmedPageId: "",
    pending: null, running: false, blocked: false };
}

/** 服务端已明确拒绝且重试没有意义：409 版本冲突要刷新，其他 4xx 要人工处理。 */
function isRetryableWriteError(error: unknown): boolean {
  if (error instanceof ApiRequestError) return error.status === 429 || error.status >= 500;
  // fetch 网络失败没有状态码；服务端可能已经提交，只能沿用同一 commandId 重试。
  return true;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "网络暂不可用";
}

/** Serialize writes with optimistic versions; a stale device must reload instead of overwriting. */
export function useCoursewareUnitProgress(coursewareId: string, publicationId: string | undefined, role: "teacher" | "student") {
  const learnerId = useLearnerId();
  const scope = `${learnerId}:${coursewareId}:${publicationId}:${role}`;
  const [state, setState] = useState<{ scope: string; unit: CoursewareUnit | null; ready: boolean; error: string }>({ scope, unit: null, ready: false, error: "" });
  const writes = useRef<WriteQueue>(newQueue(scope));

  /** 串行推进队列：先补发未确认的写入，再写最新目标页。 */
  const sync = useCallback(async (queue: WriteQueue) => {
    if (!publicationId || !queue.loaded || queue.running) return;
    queue.running = true;
    try {
      while (writes.current === queue && !queue.blocked) {
        const next = queue.pending
          ?? (queue.targetPageId && queue.targetPageId !== queue.confirmedPageId
            ? { commandId: crypto.randomUUID(), pageId: queue.targetPageId, expectedVersion: queue.version }
            : null);
        if (!next) return;
        queue.pending = next;
        try {
          const saved = await saveCoursewarePosition(coursewareId, { publicationId,
            pageId: next.pageId, expectedVersion: next.expectedVersion, commandId: next.commandId }, learnerId);
          // 切课件/卸载后旧队列已失效：结果只能丢弃，不能改新队列的版本或状态。
          if (writes.current !== queue) return;
          queue.version = saved.version;
          queue.confirmedPageId = next.pageId;
          queue.pending = null;
          setState((previous) => (previous.error ? { ...previous, error: "" } : previous));
        } catch (error) {
          if (writes.current !== queue) return;
          if (isRetryableWriteError(error)) {
            // 结果未知：保留同一 commandId/内容/版本，下一次翻页继续重试。
            setState((previous) => ({ ...previous,
              error: `学习位置暂未同步：${errorMessage(error)}。继续翻页会自动重试。` }));
            return;
          }
          queue.blocked = true;
          queue.pending = null;
          setState((previous) => ({ ...previous,
            error: `学习位置未同步：${errorMessage(error)}。请刷新目录后继续。` }));
          return;
        }
      }
    } finally {
      queue.running = false;
    }
  }, [coursewareId, learnerId, publicationId]);

  useEffect(() => {
    const controller = new AbortController();
    // 立刻换掉旧队列：切换课件/发布版时旧的在途写入不能继续补发旧页面，
    // 也不能把旧版本号或旧错误写进新作用域。
    const queue = newQueue(scope);
    writes.current = queue;
    loadCoursewareUnit(coursewareId, role, learnerId, controller.signal).then((unit) => {
      if (controller.signal.aborted || writes.current !== queue) return;
      const position = unit.lessons.find((lesson) => lesson.coursewareId === coursewareId)?.progress;
      // 打开课件不算“翻页”：已保存的页面直接视为已确认，不会凭空写一版。
      const savedPage = position && position.publicationId === publicationId ? position.pageId : "";
      const earlyTarget = queue.targetPageId;
      queue.loaded = true;
      queue.version = position?.version ?? 0;
      queue.targetPageId = earlyTarget || savedPage;
      queue.confirmedPageId = savedPage;
      setState({ scope, unit, ready: true, error: "" });
      void sync(queue);
    }).catch((error: unknown) => {
      if (controller.signal.aborted || writes.current !== queue) return;
      setState({ scope, unit: null, ready: true,
        error: error instanceof Error ? error.message : "学习位置暂时无法同步" });
    });
    return () => {
      controller.abort();
      if (writes.current === queue) writes.current = newQueue(scope);
    };
  }, [scope, coursewareId, publicationId, role, learnerId, sync]);

  const onPageChange = useCallback((pageId: string) => {
    if (role !== "student" || !publicationId) return;
    const queue = writes.current;
    if (queue.scope !== scope || queue.blocked) return;
    queue.targetPageId = pageId;
    void sync(queue);
  }, [publicationId, role, scope, sync]);

  const active = state.scope === scope;
  const position = active ? state.unit?.lessons.find((lesson) => lesson.coursewareId === coursewareId)?.progress : undefined;
  return { unit: active ? state.unit : null, ready: active && state.ready, error: active ? state.error : "",
    initialPageId: position?.available && position.publicationId === publicationId ? position.pageId : undefined, onPageChange };
}
