// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { LibraryItem } from "../../types/textbook";
import { CoursewareSourcePrepare } from "./CoursewareSourcePrepare";

afterEach(cleanup);

const upload: LibraryItem = {
  uploadId: "upload-1",
  importId: "import-1",
  filename: "New book.pdf",
  size: 200,
  status: "complete",
  questionCount: 0,
  pageCount: 30,
  chapter: "Unit 1",
  updatedAt: 1,
  materialKind: "unknown",
};

it("user Given an unclassified source When confirming its textbook type and configured subject Then source preparation starts with the selected page range", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={upload} profiles={[{ subjectId: "english", label: "English" }]} busy={false} error="" onStart={onStart} />);

  expect(screen.getByLabelText("学科")).toHaveValue("english");
  await user.selectOptions(screen.getByLabelText("材料类型"), "textbook");
  await user.selectOptions(screen.getByLabelText("学科"), "english");
  await user.clear(screen.getByLabelText("起始页"));
  await user.type(screen.getByLabelText("起始页"), "4");
  await user.clear(screen.getByLabelText("结束页"));
  await user.type(screen.getByLabelText("结束页"), "12");
  await user.click(screen.getByRole("button", { name: "自动生成教程" }));

  expect(onStart).toHaveBeenCalledWith({ autoGenerate: true, autoPublish: true, confirmedMaterialKind: "textbook", subject: "english", title: "Unit 1", pageStart: 4, pageEnd: 12, sourceTitle: "New book", distributionScope: "private" });
});

it("user Given a configured open license When recording source attribution and classroom scope Then those terms travel with source preparation", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook" }} profiles={[{ subjectId: "english", label: "English" }]} busy={false} error="" onStart={onStart} />);

  await user.selectOptions(screen.getByLabelText("学科"), "english");
  await user.click(screen.getByText("来源署名与分发许可"));
  await user.type(screen.getByLabelText("来源链接"), "https://open.example/book");
  await user.type(screen.getByLabelText("许可"), "CC BY 4.0");
  await user.type(screen.getByLabelText("署名文本"), "Author, Open Book");
  await user.selectOptions(screen.getByLabelText("允许的分发范围"), "classroom");
  expect(screen.queryByRole("checkbox", { name: /我确认这份材料用于教材课件制作/ })).not.toBeInTheDocument();
  await user.clear(screen.getByLabelText("起始页"));
  await user.type(screen.getByLabelText("起始页"), "4");
  await user.clear(screen.getByLabelText("结束页"));
  await user.type(screen.getByLabelText("结束页"), "12");
  await user.click(screen.getByRole("button", { name: "自动生成教程" }));

  expect(onStart).toHaveBeenCalledWith({ autoGenerate: true, autoPublish: true, subject: "english", title: "Unit 1", pageStart: 4, pageEnd: 12, sourceTitle: "New book", sourceUrl: "https://open.example/book", license: "CC BY 4.0", attributionText: "Author, Open Book", distributionScope: "classroom" });
});

it("user Given a short textbook and one configured subject When clicking generate Then defaults start the complete workflow without extra confirmations", async () => {
  const onStart = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={{ ...upload, pageCount: 4 }} profiles={[{ subjectId: "science", label: "科学" }]} busy={false} error="" onStart={onStart} />);
  await userEvent.click(screen.getByRole("button", { name: "自动生成教程" }));
  expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ subject: "science", pageStart: 1, pageEnd: 4, autoGenerate: true, autoPublish: true, distributionScope: "private" }));
  expect(screen.queryByRole("checkbox", { name: /我确认/ })).not.toBeInTheDocument();
});

it("user Given manual production is preferred When turning off automatic completion Then only source preparation is requested", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={upload} profiles={[{ subjectId: "science", label: "科学" }]} busy={false} error="" onStart={onStart} />);
  await user.click(screen.getByText("调整主题与制作方式（可选）"));
  await user.click(screen.getByRole("checkbox", { name: /自动完成教程/ }));
  await user.click(screen.getByRole("button", { name: "确认并准备教材来源" }));
  expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ autoGenerate: false, autoPublish: false }));
});

it("user Given a paper When opening courseware preparation Then it stays in the exam workflow", () => {
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "paper" }} profiles={[{ subjectId: "english", label: "English" }]} busy={false} error="" onStart={vi.fn(async () => undefined)} />);
  expect(screen.getByRole("alert")).toHaveTextContent("试卷不会进入教材课件流程");
  expect(screen.queryByRole("button", { name: "确认并准备教材来源" })).not.toBeInTheDocument();
});
