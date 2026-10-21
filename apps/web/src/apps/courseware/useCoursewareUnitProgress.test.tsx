// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { resetLearnerIdCacheForTests } from "../../api/identity";
import { useCoursewareUnitProgress } from "./useCoursewareUnitProgress";

const coursewareId = "lesson";
const publicationId = "pub";

type SavedPosition = { commandId: string; pageId: string; expectedVersion: number };

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function unitPayload(version = 3, pageId = "p1", id = coursewareId, publication = publicationId) {
  return {
    chapterId: "unit", title: "一次函数单元", subject: "math", resume: null,
    lessons: [{
      coursewareId: id, title: "第一课时", order: 1, pageStart: 1, pageEnd: 2, durationMinutes: 10,
      status: "published", currentBatch: true, sourceRevisionId: "rev1",
      publicationId: publication, publicationVersion: 2, activityCount: 0, answeredActivityCount: 0,
      needsCorrectionCount: 0, activityStatus: "not_started",
      progress: { coursewareId: id, publicationId: publication, pageId, version, updatedAt: 100, available: true },
    }],
  };
}

const savedPosition = (pageId: string, version: number) =>
  json({ coursewareId, publicationId, pageId, version, updatedAt: 101, available: true });

/** 记录每次保存请求体，供用例断言 commandId/版本是否被沿用。 */
function positionRequests(url: string, init?: RequestInit): SavedPosition | null {
  if (init?.method !== "PUT") return null;
  return JSON.parse(String(init.body)) as SavedPosition;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  resetLearnerIdCacheForTests();
});

it("user Given a lost write response When the learner turns another page Then the retry reuses the same command and later pages keep syncing", async () => {
  const puts: SavedPosition[] = [];
  let version = 3;
  let loseFirstResponse = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return json(unitPayload());
    puts.push(body);
    if (loseFirstResponse) {
      loseFirstResponse = false;
      // 服务端可能已提交，但响应在网络中丢失；客户端拿不到任何状态码。
      throw new TypeError("Failed to fetch");
    }
    version += 1;
    return savedPosition(body.pageId, version);
  }));

  const { result } = renderHook(() => useCoursewareUnitProgress(coursewareId, publicationId, "student"));
  await waitFor(() => expect(result.current.ready).toBe(true));

  act(() => result.current.onPageChange("p2"));
  await waitFor(() => expect(puts).toHaveLength(1));
  await waitFor(() => expect(result.current.error).toContain("自动重试"));

  act(() => result.current.onPageChange("p3"));
  await waitFor(() => expect(puts).toHaveLength(3));

  // 先原样补发未知结果的那一版，再用服务端返回的新版本写当前页。
  expect(puts[1]).toEqual(puts[0]);
  expect(puts[2]).toMatchObject({ pageId: "p3", expectedVersion: 4 });
  expect(puts[2].commandId).not.toBe(puts[0].commandId);
  await waitFor(() => expect(result.current.error).toBe(""));
});

it("user Given a temporarily unavailable position service When the learner continues Then syncing resumes without a refresh", async () => {
  const puts: SavedPosition[] = [];
  let version = 7;
  let serviceDown = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return json(unitPayload(version, "p1"));
    puts.push(body);
    if (serviceDown) {
      serviceDown = false;
      return json({ detail: "服务暂时不可用" }, 503);
    }
    version += 1;
    return savedPosition(body.pageId, version);
  }));

  const { result } = renderHook(() => useCoursewareUnitProgress(coursewareId, publicationId, "student"));
  await waitFor(() => expect(result.current.ready).toBe(true));

  act(() => result.current.onPageChange("p2"));
  await waitFor(() => expect(result.current.error).toContain("自动重试"));

  act(() => result.current.onPageChange("p3"));
  await waitFor(() => expect(puts).toHaveLength(3));
  expect(puts[1]).toEqual(puts[0]);
  expect(puts[2]).toMatchObject({ pageId: "p3", expectedVersion: 8 });
  await waitFor(() => expect(result.current.error).toBe(""));
});

it("user Given another device saved a newer position When the learner turns a page Then writes stop and the page asks for a refresh", async () => {
  const puts: SavedPosition[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return json(unitPayload());
    puts.push(body);
    return json({ detail: "学习位置已变化，请刷新单元目录后继续" }, 409);
  }));

  const { result } = renderHook(() => useCoursewareUnitProgress(coursewareId, publicationId, "student"));
  await waitFor(() => expect(result.current.ready).toBe(true));

  act(() => result.current.onPageChange("p2"));
  await waitFor(() => expect(result.current.error).toContain("请刷新目录后继续"));

  act(() => result.current.onPageChange("p3"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  // 冲突后不能再写，否则会用过期版本覆盖其他设备的位置。
  expect(puts).toHaveLength(1);
});

it("user Given two quick page turns When the first write is still in flight Then writes stay serial and versions advance once each", async () => {
  let releaseFirst: ((response: Response) => void) | undefined;
  const puts: SavedPosition[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return Promise.resolve(json(unitPayload()));
    puts.push(body);
    if (puts.length === 1) return new Promise<Response>((resolve) => { releaseFirst = resolve; });
    return Promise.resolve(savedPosition(body.pageId, body.expectedVersion + 1));
  }));

  const { result } = renderHook(() => useCoursewareUnitProgress(coursewareId, publicationId, "student"));
  await waitFor(() => expect(result.current.ready).toBe(true));

  act(() => result.current.onPageChange("p2"));
  await waitFor(() => expect(puts).toHaveLength(1));
  act(() => result.current.onPageChange("p3"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(puts).toHaveLength(1);

  await act(async () => { releaseFirst?.(savedPosition("p2", 4)); await Promise.resolve(); });
  await waitFor(() => expect(puts).toHaveLength(2));
  expect(puts[1]).toMatchObject({ pageId: "p3", expectedVersion: 4 });
});

it("user Given a slow save on one courseware When switching to another Then the old queue stops and the new one starts from its own version", async () => {
  const puts: SavedPosition[] = [];
  let releaseOld: ((response: Response) => void) | undefined;
  let releaseNewUnit: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) {
      // 新课件的目录请求故意挂起：旧队列必须在“新作用域尚不可用”时就失效。
      if (url.includes("/courseware/lesson-b/")) {
        return new Promise<Response>((resolve) => { releaseNewUnit = resolve; });
      }
      return Promise.resolve(json(unitPayload(3, "a1", "lesson-a", "pub-a")));
    }
    puts.push(body);
    if (body.pageId === "a2") return new Promise<Response>((resolve) => { releaseOld = resolve; });
    return Promise.resolve(savedPosition(body.pageId, body.expectedVersion + 1));
  }));

  const view = renderHook(({ id, publication }: { id: string; publication: string }) =>
    useCoursewareUnitProgress(id, publication, "student"), { initialProps: { id: "lesson-a", publication: "pub-a" } });
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  act(() => view.result.current.onPageChange("a2"));
  await waitFor(() => expect(puts).toHaveLength(1));
  // 第二页排在挂起的写入后面，还没发出。
  act(() => view.result.current.onPageChange("a3"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(puts).toHaveLength(1);

  view.rerender({ id: "lesson-b", publication: "pub-b" });
  // 新课件目录还在加载：此时旧队列就必须已经失效。
  expect(view.result.current.ready).toBe(false);
  await act(async () => { releaseOld?.(savedPosition("a2", 4)); await Promise.resolve(); });

  // 旧响应既不能补发旧课件的 a3，也不能写入新课件状态。
  expect(puts.map((put) => put.pageId)).toEqual(["a2"]);
  expect(view.result.current.error).toBe("");
  expect(view.result.current.ready).toBe(false);

  await act(async () => { releaseNewUnit?.(json(unitPayload(5, "b1", "lesson-b", "pub-b"))); await Promise.resolve(); });
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  // 位置取自新课件目录自己的版本与页面。
  expect(view.result.current.initialPageId).toBe("b1");

  act(() => view.result.current.onPageChange("b2"));
  await waitFor(() => expect(puts).toHaveLength(2));
  expect(puts[1]).toMatchObject({ pageId: "b2", expectedVersion: 5 });
  expect(puts[1].commandId).not.toBe(puts[0].commandId);
});

it("user Given a failing save on the old courseware When switching Then the failure neither blocks nor retries on the new courseware", async () => {
  const puts: SavedPosition[] = [];
  let failOld: ((reason: unknown) => void) | undefined;
  const unitFor = (url: string) => url.includes("/courseware/lesson-b/")
    ? unitPayload(5, "b1", "lesson-b", "pub-b") : unitPayload(3, "a1", "lesson-a", "pub-a");
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return Promise.resolve(json(unitFor(url)));
    puts.push(body);
    if (body.pageId === "a2") {
      return new Promise<Response>((_resolve, reject) => { failOld = reject; });
    }
    return Promise.resolve(savedPosition(body.pageId, body.expectedVersion + 1));
  }));

  const view = renderHook(({ id, publication }: { id: string; publication: string }) =>
    useCoursewareUnitProgress(id, publication, "student"), { initialProps: { id: "lesson-a", publication: "pub-a" } });
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  act(() => view.result.current.onPageChange("a2"));
  await waitFor(() => expect(puts).toHaveLength(1));

  view.rerender({ id: "lesson-b", publication: "pub-b" });
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  await act(async () => { failOld?.(new TypeError("Failed to fetch")); await Promise.resolve(); });

  // 旧课件的网络失败不能变成新课件的“待重试”状态或错误提示。
  expect(view.result.current.error).toBe("");
  act(() => view.result.current.onPageChange("b2"));
  await waitFor(() => expect(puts).toHaveLength(2));
  expect(puts.map((put) => put.pageId)).toEqual(["a2", "b2"]);
  expect(puts[1]).toMatchObject({ pageId: "b2", expectedVersion: 5 });
  await waitFor(() => expect(view.result.current.error).toBe(""));
});

it("user leaves the lesson while a save is in flight Then the late response cannot keep writing", async () => {
  const puts: SavedPosition[] = [];
  let release: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = positionRequests(url, init);
    if (!body) return Promise.resolve(json(unitPayload()));
    puts.push(body);
    return new Promise<Response>((resolve) => { release = resolve; });
  }));

  const view = renderHook(() => useCoursewareUnitProgress(coursewareId, publicationId, "student"));
  await waitFor(() => expect(view.result.current.ready).toBe(true));
  act(() => view.result.current.onPageChange("p2"));
  await waitFor(() => expect(puts).toHaveLength(1));
  // 第二页已排队在挂起写入后面：卸载后旧队列不得再把 p3 发出去。
  act(() => view.result.current.onPageChange("p3"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(puts.map((put) => put.pageId)).toEqual(["p2"]);
  view.unmount();

  await act(async () => { release?.(savedPosition("p2", 4)); await Promise.resolve(); });
  expect(puts.map((put) => put.pageId)).toEqual(["p2"]);
});
