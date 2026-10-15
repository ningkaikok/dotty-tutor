// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { explainCoursewareSource, searchCoursewareSource, type CoursewareSourceEvidence } from "../../api/coursewareSearch";
import { SourceSearchPanel } from "./SourceSearchPanel";
import { ApiRequestError } from "../../api/client";
import { setCurrentLearnerId } from "../../api/identity";

vi.mock("../../api/coursewareSearch", () => ({ searchCoursewareSource: vi.fn(), explainCoursewareSource: vi.fn() }));

const evidence: CoursewareSourceEvidence = {
  documentId: "evidence-1", coursewareId: "course-1", publicationId: "publication-1", publicationVersion: 2,
  sourceRevisionId: "source-1", sourcePage: 12, coursewarePageId: "page-1", title: "函数",
  excerpt: "一次函数的图像是一条直线。", previewUrl: "/api/courseware/course-1/source-search/evidence/evidence-1/preview?publicationId=publication-1",
  sourceRef: { sourceRevisionId: "source-1", page: 12, sentenceIds: ["s-1"] }, rank: 1,
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => { vi.mocked(searchCoursewareSource).mockResolvedValue({ items: [evidence] }); });

function enterQuestion() {
  fireEvent.click(screen.getByText("查教材原文与讲解依据"));
  fireEvent.change(screen.getByLabelText("教材关键词或问题"), { target: { value: "一次函数" } });
}

describe("教材依据", () => {
  it("user searches original text and opens its publication-bound original page", async () => {
    // Given a published courseware, When searching, Then display the exact excerpt and original-page link.
    render(<SourceSearchPanel coursewareId="course-1" publicationId="publication-1" />);
    enterQuestion();
    fireEvent.click(screen.getByRole("button", { name: "查找原文" }));
    expect(await screen.findByText(evidence.excerpt)).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看教材原页 12" }).getAttribute("href")).toContain("publicationId=publication-1");
  });

  it("user sees original evidence when the optional explanation model is unavailable", async () => {
    // Given a model outage, When asking for explanation, Then keep the original excerpt available.
    vi.mocked(explainCoursewareSource).mockResolvedValue({ status: "unavailable", explanation: "讲解模型暂不可用", evidence: [evidence] });
    render(<SourceSearchPanel coursewareId="course-1" publicationId="publication-1" />);
    enterQuestion();
    fireEvent.click(screen.getByRole("button", { name: "依据原文讲解" }));
    expect(await screen.findByText("讲解模型暂不可用")).toBeTruthy();
    expect(screen.getByText(evidence.excerpt)).toBeTruthy();
  });

  it("user switches publication without seeing evidence from the previous version", async () => {
    // Given displayed search results, When the publication changes, Then hide all previous evidence.
    const view = render(<SourceSearchPanel coursewareId="course-1" publicationId="publication-1" />);
    enterQuestion();
    fireEvent.click(screen.getByRole("button", { name: "查找原文" }));
    await screen.findByText(evidence.excerpt);
    view.rerender(<SourceSearchPanel coursewareId="course-1" publicationId="publication-2" />);
    await waitFor(() => expect(screen.queryByText(evidence.excerpt)).toBeNull());
    expect((screen.getByLabelText("教材关键词或问题") as HTMLInputElement).value).toBe("");
  });

  it("user switches learner without seeing the previous question or source results", async () => {
    // Given another learner's query, When changing identity, Then clear its question and evidence immediately.
    act(() => setCurrentLearnerId("search-student-a"));
    render(<SourceSearchPanel coursewareId="course-1" publicationId="publication-1" />);
    enterQuestion();
    fireEvent.click(screen.getByRole("button", { name: "查找原文" }));
    await screen.findByText(evidence.excerpt);
    act(() => setCurrentLearnerId("search-student-b"));
    expect(screen.queryByText(evidence.excerpt)).toBeNull();
    expect((screen.getByLabelText("教材关键词或问题") as HTMLInputElement).value).toBe("");
  });

  it.each([403, 404])("user loses source access (%s) without retaining old evidence or an embedded preview", async (status) => {
    // Given old source results, When a new read is denied, Then remove the excerpt and source link.
    render(<SourceSearchPanel coursewareId="course-1" publicationId="publication-1" />);
    enterQuestion();
    fireEvent.click(screen.getByRole("button", { name: "查找原文" }));
    await screen.findByText(evidence.excerpt);
    expect(screen.queryByRole("img")).toBeNull();
    vi.mocked(searchCoursewareSource).mockRejectedValueOnce(new ApiRequestError("来源已撤回", status));
    fireEvent.click(screen.getByRole("button", { name: "查找原文" }));
    await screen.findByText(/来源已撤回/);
    expect(screen.queryByText(evidence.excerpt)).toBeNull();
    expect(screen.queryByRole("link", { name: "查看教材原页 12" })).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
