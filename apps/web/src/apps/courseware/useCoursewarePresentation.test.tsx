// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { useCoursewarePresentation } from "./useCoursewarePresentation";
import { CoursewareExport } from "./CoursewareExport";

const publicDocument = {
  coursewareId: "lesson", publicationId: "pub", version: 1, title: "学生公开内容", subject: "math", durationMinutes: 10,
  sections: [], assets: [], sourceDistributionScope: "classroom", pages: [{
    pageId: "p1", sectionId: "s1", pageKind: "explanation", layout: "text_focus", title: "公开页",
    objectiveIds: [], sourceRefs: [], estimatedMinutes: 10, blocks: [],
  }],
};
const teacherDocument = { ...publicDocument, title: "教师授课内容", pages: publicDocument.pages.map((page) => ({ ...page, teacherNotes: "私有教师答案" })) };
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });

function Probe({ role, exposures }: { role: "teacher" | "student"; exposures: string[] }) {
  const state = useCoursewarePresentation("lesson", "pub", role);
  const [message, setMessage] = useState("");
  useLayoutEffect(() => {
    if (role === "student" && state.document?.canRevealAnswers) exposures.push("学生看到教师投影");
  });
  return <div><p>{state.document?.title ?? "读取中"}</p><p>{state.document?.pages[0]?.teacherNotes}</p>
    <button type="button" disabled={!state.submitPractice} onClick={() => void state.submitPractice?.("a1", { text: "5" }).then(() => setMessage("已记录")).catch(() => setMessage("请重试"))}>提交</button><p>{message}</p></div>;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it("user Given a teacher presentation When switching to a student route Then no render exposes the former private projection", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/presentation")
    ? json({ ...teacherDocument, document: teacherDocument }) : json(publicDocument)));
  const exposures: string[] = [];
  const view = render(<Probe role="teacher" exposures={exposures} />);
  expect(await screen.findByText("私有教师答案")).toBeVisible();
  view.rerender(<Probe role="student" exposures={exposures} />);
  expect(await screen.findByText("学生公开内容")).toBeVisible();
  expect(screen.queryByText("私有教师答案")).not.toBeInTheDocument();
  expect(exposures).toEqual([]);
});

it("user Given a teacher request still running When switching roles Then its late response cannot replace the student publication", async () => {
  let completeTeacher: ((value: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn((url: string) => url.includes("/presentation")
    ? new Promise<Response>((resolve) => { completeTeacher = resolve; }) : Promise.resolve(json(publicDocument))));
  const exposures: string[] = [];
  const view = render(<Probe role="teacher" exposures={exposures} />);
  view.rerender(<Probe role="student" exposures={exposures} />);
  expect(await screen.findByText("学生公开内容")).toBeVisible();
  await act(async () => { completeTeacher?.(json({ ...teacherDocument, document: teacherDocument })); });
  expect(screen.getByText("学生公开内容")).toBeVisible();
  expect(exposures).toEqual([]);
});

it("user Given a lost attempt response When retrying the same answer Then the original idempotency command is reused", async () => {
  const commands: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method !== "POST") return json(publicDocument);
    commands.push((JSON.parse(String(init.body)) as { commandId: string }).commandId);
    if (commands.length === 1) throw new TypeError("Response lost after commit");
    return json({ assessment: "correct", feedback: "已记录" });
  }));
  render(<Probe role="student" exposures={[]} />);
  await screen.findByText("学生公开内容");
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await screen.findByText("请重试");
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await screen.findByText("已记录");
  expect(commands).toHaveLength(2);
  expect(commands[1]).toBe(commands[0]);
});

it("user Given an answer export still running When switching to student scope Then its late response cannot reveal or print teacher answers", async () => {
  let complete: ((value: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { complete = resolve; })));
  const view = render(<CoursewareExport coursewareId="lesson" publicationId="pub" role="teacher" />);
  fireEvent.click(screen.getByRole("button", { name: "导出教师答案版" }));
  view.rerender(<CoursewareExport coursewareId="lesson" publicationId="pub" role="student" />);
  await act(async () => { complete?.(json({ ...teacherDocument, document: teacherDocument })); });
  await waitFor(() => expect(screen.getByRole("button", { name: "导出学生版 / PDF" })).toBeEnabled());
  expect(screen.queryByRole("region", { name: "课件打印预览" })).not.toBeInTheDocument();
  expect(screen.queryByText("私有教师答案")).not.toBeInTheDocument();
});
