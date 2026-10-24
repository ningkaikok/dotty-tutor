// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetLearnerIdCacheForTests, setCurrentLearnerId } from "../../api/identity";
import { StudentLearningApp } from "./StudentLearningApp";

const assignment = (title: string, learnerStatus: string) => ({
  assignmentId: title, publicationId: "paper-1", title, learnerStatus,
  className: "一班", attemptedCount: 1, questionCount: 1, dueAt: null,
});

function serve(load: (url: URL) => unknown | Promise<unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const result = await load(new URL(input, "http://localhost"));
    return new Response(JSON.stringify(result));
  }));
}

function empty(url: URL): unknown {
  if (url.pathname === "/api/learning/next") return {
    learnerId: "student-a",
    serverTime: 1,
    next: { kind: "none" },
    summary: { unfinishedAssignments: 0, pendingConfirmationCount: 0, unmasteredCount: 0, dueReviewCount: 0 },
  };
  return url.pathname === "/api/progress" ? { dueReviewCount: 0 } : { items: [] };
}

function openToday() {
  render(<MemoryRouter><StudentLearningApp /></MemoryRouter>);
}

describe("user: 学生只看到属于自己的待办和可回看的作业", () => {
  beforeEach(() => { localStorage.clear(); resetLearnerIdCacheForTests(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetLearnerIdCacheForTests(); });

  it("Given 已完成和未完成作业 When 打开今日 Then 只把未完成作业计入待办，完成作业仍可回看", async () => {
    serve((url) => url.pathname === "/api/assignments"
      ? { items: [assignment("待做作业", "in_progress"), assignment("完成作业", "completed")] }
      : empty(url));
    openToday();
    expect(await screen.findByRole("heading", { name: "今天有 1 件事" })).toBeInTheDocument();
    const queue = screen.getByRole("list", { name: "今日任务队列" });
    expect(within(queue).getByText("待做作业")).toBeInTheDocument();
    expect(within(queue).queryByText("完成作业")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回看：完成作业" })).toBeInTheDocument();
  });

  it("Given 所有作业已完成 When 打开今日 Then 待办清空且回看入口保留", async () => {
    serve((url) => url.pathname === "/api/assignments"
      ? { items: [assignment("完成作业", "completed")] } : empty(url));
    openToday();
    expect(await screen.findByRole("heading", { name: "今天没有待办任务" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "今日任务队列" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回看：完成作业" })).toBeInTheDocument();
  });

  it("Given 部分数据不可读 When 打开今日 Then 不把读不到的任务说成已完成", async () => {
    serve((url) => {
      if (url.pathname === "/api/mistakes") throw new Error("错题暂不可读");
      return empty(url);
    });
    openToday();
    expect(await screen.findByRole("alert")).toHaveTextContent("错题暂不可读");
    expect(screen.queryByRole("heading", { name: "今天没有待办任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "已加载的内容中没有待办" })).toBeInTheDocument();
  });

  it("Given 四路数据都不可读 When 打开今日 Then 展示失败而非空任务或空练习目录", async () => {
    serve((url) => {
      if (url.pathname === "/api/learners") return { items: [] };
      throw new Error("网络不可用");
    });
    openToday();
    expect(await screen.findByRole("heading", { name: "暂时读不到今天的任务" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "练习" })).not.toBeInTheDocument();
  });

  it("Given 甲的任务已加载 When 切换到乙且作业请求失败 Then 加载中和完成后都不显示甲的任务", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    serve(async (url) => {
      if (url.pathname === "/api/assignments") {
        if (url.searchParams.get("learnerId") === "student-b") {
          await pending;
          throw new Error("乙的作业暂不可读");
        }
        return { items: [assignment("甲的作业", "in_progress")] };
      }
      return empty(url);
    });
    openToday();
    expect(await screen.findByText("甲的作业")).toBeInTheDocument();
    act(() => setCurrentLearnerId("student-b"));
    expect(screen.queryByText("甲的作业")).not.toBeInTheDocument();
    await act(async () => release());
    expect(await screen.findByRole("alert")).toHaveTextContent("乙的作业暂不可读");
    expect(screen.queryByText("甲的作业")).not.toBeInTheDocument();
  });

  it("Given 服务端已有未完成的作业会话 When 打开统一入口 Then 只读显示继续入口，点击后才启动连续学习", async () => {
    let started = false;
    serve((url) => {
      if (url.pathname === "/api/learning/next") return {
        learnerId: "local-demo", serverTime: 1,
        next: { kind: "assignment", assignmentId: "assignment-1", publicationId: "paper-1", title: "正在做的作业", learnerStatus: "in_progress", sessionId: "server-session" },
        summary: { unfinishedAssignments: 1, pendingConfirmationCount: 0, unmasteredCount: 0, dueReviewCount: 0 },
      };
      if (url.pathname === "/api/learning/next/start") { started = true; return { next: { kind: "assignment" } }; }
      return empty(url);
    });
    openToday();

    expect(await screen.findByRole("button", { name: "继续学习：正在做的作业" })).toBeInTheDocument();
    expect(started).toBe(false);
  });

  it("Given 旧已发布错题和生成中的到期复习 When 打开今日 Then 展示服务端汇总且旧错题进入订正而非人工确认", async () => {
    serve((url) => {
      if (url.pathname === "/api/mistakes") return { items: [{
        mistakeId: "legacy-published",
        learnerId: "local-demo",
        contentType: "application/vnd.dotty.publication+json",
        status: "pending_confirmation",
      }] };
      if (url.pathname === "/api/progress") return { dueReviewCount: 0 };
      if (url.pathname === "/api/learning/next") return {
        learnerId: "local-demo",
        serverTime: 1,
        next: { kind: "review", taskId: "review-1", status: "generating", title: "分数加减" },
        summary: { unfinishedAssignments: 0, pendingConfirmationCount: 0, unmasteredCount: 1, dueReviewCount: 1 },
      };
      return empty(url);
    });

    openToday();

    expect(await screen.findByRole("heading", { name: "今天有 2 件事" })).toBeInTheDocument();
    expect(screen.getByText("订正错题")).toBeInTheDocument();
    expect(screen.getByText("今日复习")).toBeInTheDocument();
    expect(screen.queryByText("确认新录入的错题")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "去确认：确认新录入的错题" })).not.toBeInTheDocument();
  });
});
