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

  expect(screen.getByLabelText("学科")).toHaveValue("");
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
  render(<CoursewareSourcePrepare upload={{ ...upload, pageCount: 4 }} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={onStart} />);
  await userEvent.click(screen.getByRole("button", { name: "自动生成教程" }));
  expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ subject: "science", pageStart: 1, pageEnd: 4, autoGenerate: true, autoPublish: true, distributionScope: "private" }));
  expect(screen.queryByRole("checkbox", { name: /我确认/ })).not.toBeInTheDocument();
});

it("user Given a confirmed 61-page textbook When choosing whole-book production Then it sends one explicit request without a page range", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  const onStartWholeBook = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook", pageCount: 61 }} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={onStart} onStartWholeBook={onStartWholeBook} />);

  await user.click(screen.getByRole("radio", { name: "整本自动续做（61 页）" }));
  expect(screen.queryByLabelText("起始页")).not.toBeInTheDocument();
  expect(screen.getByText(/最多 1000 个原 PDF 物理页.*每批最多 30 个物理页.*最多 34 批.*最多制作 45 张课件页/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "开始整本自动制作" }));

  expect(onStart).not.toHaveBeenCalled();
  expect(onStartWholeBook).toHaveBeenCalledWith({
    mode: "whole_book", subject: "science", title: "Unit 1", sourceTitle: "New book", distributionScope: "private",
  });
});

it("user Given a confirmed 773-page textbook When choosing whole-book production Then the accepted upload can start without narrowing its page range", async () => {
  const user = userEvent.setup();
  const onStartWholeBook = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook", pageCount: 773 }} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={vi.fn(async () => undefined)} onStartWholeBook={onStartWholeBook} />);

  const wholeBookOption = screen.getByRole("radio", { name: "整本自动续做（773 页）" });
  expect(wholeBookOption).toBeEnabled();
  await user.click(wholeBookOption);
  await user.click(screen.getByRole("button", { name: "开始整本自动制作" }));

  expect(onStartWholeBook).toHaveBeenCalledWith({
    mode: "whole_book", subject: "science", title: "Unit 1", sourceTitle: "New book", distributionScope: "private",
  });
});

it("user Given a 773-page textbook When opening new production Then whole-book is recommended without starting until the explicit action", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  const onStartWholeBook = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook", pageCount: 773 }} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={onStart} onStartWholeBook={onStartWholeBook} />);

  expect(screen.getByRole("radio", { name: "整本自动续做（773 页）" })).toBeChecked();
  expect(onStartWholeBook).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "开始整本自动制作" }));
  expect(onStartWholeBook).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("radio", { name: "制作指定页段（每次最多 30 页）" }));
  await user.clear(screen.getByLabelText("起始页"));
  await user.type(screen.getByLabelText("起始页"), "45");
  await user.clear(screen.getByLabelText("结束页"));
  await user.type(screen.getByLabelText("结束页"), "60");
  await user.click(screen.getByRole("radio", { name: "整本自动续做（773 页）" }));
  await user.click(screen.getByRole("radio", { name: "制作指定页段（每次最多 30 页）" }));
  expect(screen.getByLabelText("起始页")).toHaveValue(45);
  expect(screen.getByLabelText("结束页")).toHaveValue(60);
});

it("user Given a 1000-page textbook at the whole-book boundary When choosing a production scope Then whole-book mode remains available", () => {
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook", pageCount: 1000 }} profiles={[{ subjectId: "english", label: "English" }]} initialSubject="english" busy={false} error="" onStart={vi.fn(async () => undefined)} onStartWholeBook={vi.fn(async () => undefined)} />);
  expect(screen.getByRole("radio", { name: "整本自动续做（1000 页）" })).toBeEnabled();
  expect(screen.queryByText(/最多支持 1000 页；这份教材有/)).not.toBeInTheDocument();
});

it("user Given a textbook beyond the 1000-page whole-book limit When choosing a production range Then whole-book mode is unavailable with a clear page limit", () => {
  render(<CoursewareSourcePrepare upload={{ ...upload, materialKind: "textbook", pageCount: 1001 }} profiles={[{ subjectId: "english", label: "English" }]} initialSubject="english" busy={false} error="" onStart={vi.fn(async () => undefined)} onStartWholeBook={vi.fn(async () => undefined)} />);
  expect(screen.getByRole("radio", { name: "整本自动续做（1001 页）" })).toBeDisabled();
  expect(screen.getByText("整本自动制作最多支持 1000 页；这份教材有 1001 页。可按原有流程制作指定页段。")).toBeInTheDocument();
});

it("user Given the library API returns an unknown page count as null When opening courseware preparation Then the page count is treated as unknown", () => {
  const uploadFromApi = JSON.parse(JSON.stringify({ ...upload, materialKind: "textbook", pageCount: null })) as LibraryItem;
  render(<CoursewareSourcePrepare upload={uploadFromApi} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={vi.fn(async () => undefined)} onStartWholeBook={vi.fn(async () => undefined)} />);

  expect(screen.getByRole("radio", { name: "整本自动续做（需先识别物理页数）" })).toBeDisabled();
  expect(screen.getByLabelText("起始页")).not.toHaveAttribute("max");
  expect(screen.queryByText(/这份教材有 .* 页/)).not.toBeInTheDocument();
  expect(screen.queryByText(/整本自动续做（null 页）/)).not.toBeInTheDocument();
});

it("user Given manual production is preferred When turning off automatic completion Then only source preparation is requested", async () => {
  const user = userEvent.setup();
  const onStart = vi.fn(async () => undefined);
  render(<CoursewareSourcePrepare upload={upload} profiles={[{ subjectId: "science", label: "科学" }]} initialSubject="science" busy={false} error="" onStart={onStart} />);
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
