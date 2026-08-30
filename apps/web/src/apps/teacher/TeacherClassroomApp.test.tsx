// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TeacherClassroomApp } from "./TeacherClassroomApp";

const assignment = {
  assignmentId: "assignment-1",
  classId: "class-1",
  publicationId: "paper-1",
  title: "一次函数练习",
  publicationTitle: "一次函数练习",
  dueAt: null,
  status: "active",
  lessonIds: ["question-1"],
  questionCount: 1,
  createdAt: 1,
  updatedAt: 1,
};

describe("TeacherClassroomApp", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/classes") return { ok: true, json: async () => ({ items: [{ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, createdAt: 1, updatedAt: 1 }] }) };
      if (url === "/api/publications?status=published") return { ok: true, json: async () => ({ items: [{ publicationId: "paper-1", title: "一次函数练习", status: "published", version: 1, lessonIds: ["question-1"], lessonCount: 1, createdAt: 1, updatedAt: 1 }] }) };
      if (url === "/api/classes/class-1") return { ok: true, json: async () => ({ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, members: [{ learnerId: "local-demo", displayName: "小安", joinedAt: 1 }], assignments: [assignment], createdAt: 1, updatedAt: 1 }) };
      return { ok: true, json: async () => ({ class: { classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中" }, assignment, summary: { memberCount: 1, startedCount: 1, completedCount: 1, completionRate: 1 }, students: [{ learnerId: "local-demo", displayName: "小安", sessionId: "session-1", attemptedCount: 1, questionCount: 1, progress: 1, status: "completed", averageMastery: 0.6 }], knowledgePoints: [{ knowledgePointId: "kp-1", knowledgePoint: "一次函数", observedStudentCount: 1, averageScore: 0.6, distribution: { notStarted: 0, needsSupport: 0, developing: 1, mastered: 0 } }], metricDefinition: "掌握度只统计已有作答证据的学生；未开始不等于掌握度为 0。" }) };
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows assignment progress and the knowledge point distribution", async () => {
    render(<MemoryRouter initialEntries={["/teacher"]}><TeacherClassroomApp /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "班级学习进展" })).toBeVisible();
    expect(await screen.findByRole("heading", { name: "一次函数练习" })).toBeVisible();
    expect(screen.getByText("已掌握")).toBeVisible();
    expect(screen.getByRole("note")).toHaveTextContent("未开始不等于掌握度为 0");
  });

  it("reports a failed dashboard instead of leaving the area blank", async () => {
    // 班级有作业但看板请求失败时，旧实现既不渲染看板也不渲染空态提示，
    // 老师只能看到一片空白，无从判断是没有数据还是加载失败。
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/classes") return { ok: true, json: async () => ({ items: [{ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, createdAt: 1, updatedAt: 1 }] }) };
      if (url === "/api/publications?status=published") return { ok: true, json: async () => ({ items: [] }) };
      if (url === "/api/classes/class-1") return { ok: true, json: async () => ({ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, members: [{ learnerId: "local-demo", displayName: "小安", joinedAt: 1 }], assignments: [assignment], createdAt: 1, updatedAt: 1 }) };
      return { ok: false, status: 500, json: async () => ({ detail: "看板服务不可用" }) };
    }));

    render(<MemoryRouter initialEntries={["/teacher"]}><TeacherClassroomApp /></MemoryRouter>);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("掌握度看板加载失败");
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
    // 有作业时不能再退回“先布置一份作业”的提示，那会把加载失败说成没有数据。
    expect(screen.queryByText(/先布置一份作业/)).toBeNull();
  });

  it("offers an assignment picker once a class has more than one assignment", async () => {
    // 后端一直支持按作业查看看板，之前 UI 没有入口，老师只能看到默认那一次。
    const second = { ...assignment, assignmentId: "assignment-2", title: "二次函数练习" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/classes") return { ok: true, json: async () => ({ items: [{ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, createdAt: 1, updatedAt: 1 }] }) };
      if (url === "/api/publications?status=published") return { ok: true, json: async () => ({ items: [] }) };
      if (url === "/api/classes/class-1") return { ok: true, json: async () => ({ classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中", memberCount: 1, members: [{ learnerId: "local-demo", displayName: "小安", joinedAt: 1 }], assignments: [assignment, second], createdAt: 1, updatedAt: 1 }) };
      return { ok: true, json: async () => ({ class: { classId: "class-1", name: "初二数学一班", subject: "数学", gradeBand: "初中" }, assignment, summary: { memberCount: 1, startedCount: 1, completedCount: 1, completionRate: 1 }, students: [], knowledgePoints: [], metricDefinition: "掌握度只统计已有作答证据的学生；未开始不等于掌握度为 0。" }) };
    }));

    render(<MemoryRouter initialEntries={["/teacher"]}><TeacherClassroomApp /></MemoryRouter>);

    const picker = await screen.findByLabelText("查看作业");
    expect(picker).toBeVisible();
    expect(screen.getByRole("option", { name: "二次函数练习" })).toBeInTheDocument();
  });
});
