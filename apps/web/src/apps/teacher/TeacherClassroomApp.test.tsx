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
});
