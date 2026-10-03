// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "../../api/client";
import type { ChapterManagement } from "../../types/chapter";
import type { BackgroundJob } from "../../types/textbook";

const api = vi.hoisted(() => ({
  loadChapter: vi.fn(), generateChapterAi: vi.fn(), loadChapterAiJob: vi.fn(), cancelChapterAiJob: vi.fn(), retryChapterAiJob: vi.fn(),
  generateChapter: vi.fn(), editChapterLesson: vi.fn(), publishChapter: vi.fn(), reviseChapter: vi.fn(), reviewChapterLesson: vi.fn(),
}));
vi.mock("../../api/chapters", () => api);

import { useChapterStudio } from "./useChapterStudio";

const chapter = (chapterId = "chapter-1", extra: Partial<ChapterManagement> = {}): ChapterManagement => ({
  chapterId, subject: "math", title: chapterId, status: "draft", version: 1, recordVersion: 7,
  sourceRevisions: [], lessons: [], currentLessonIds: [], reviewIssues: [], publicationId: null, publications: [], ...extra,
});
const job = (status: BackgroundJob["status"], extra: Partial<BackgroundJob> = {}): BackgroundJob => ({
  jobId: "job-1", jobType: "chapter.lesson.generate", status, progress: status === "running" ? 42 : 0,
  message: status === "running" ? "正在按来源生成" : "已排队", attemptCount: 1, maxAttempts: 3,
  cancelRequested: false, createdAt: 1, updatedAt: 1, ...extra,
});

describe("useChapterStudio AI generation", () => {
  beforeEach(() => { api.loadChapterAiJob.mockResolvedValue(job("running")); });
  afterEach(() => { vi.clearAllMocks(); });

  it("user Given a loaded chapter When starting an AI draft Then the running progress is available to the review screen", async () => {
    api.loadChapter.mockResolvedValue(chapter());
    api.generateChapterAi.mockResolvedValue(job("running"));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));
    await waitFor(() => expect(result.current.chapter?.chapterId).toBe("chapter-1"));

    await act(async () => { await result.current.startAiGeneration(); });

    expect(result.current.aiJob).toMatchObject({ status: "running", progress: 42, message: "正在按来源生成" });
  });

  it("user Given a queued generation When cancelling it Then the review screen receives the cancelled state", async () => {
    api.loadChapter.mockResolvedValue(chapter());
    api.generateChapterAi.mockResolvedValue(job("queued"));
    api.cancelChapterAiJob.mockResolvedValue(job("cancelled"));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));
    await waitFor(() => expect(result.current.chapter?.chapterId).toBe("chapter-1"));
    await act(async () => { await result.current.startAiGeneration(); });
    await act(async () => { await result.current.cancelAiGeneration(); });

    expect(result.current.aiJob?.status).toBe("cancelled");
  });

  it("user Given an AI generation error When retrying the failed job Then it returns to the queued state", async () => {
    api.loadChapter.mockResolvedValue(chapter());
    api.generateChapterAi.mockResolvedValue(job("failed", { message: "服务暂不可用", lastError: { message: "服务暂不可用" } }));
    api.retryChapterAiJob.mockResolvedValue(job("queued", { progress: 0, message: "已重新排队" }));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));
    await waitFor(() => expect(result.current.chapter?.chapterId).toBe("chapter-1"));
    await act(async () => { await result.current.startAiGeneration(); });
    expect(result.current.error).toContain("服务暂不可用");

    await act(async () => { await result.current.retryAiGeneration(); });

    expect(result.current.aiJob?.status).toBe("queued");
    expect(result.current.error).toBe("");
  });

  it("user Given a running AI draft When the job completes Then the new lessons appear after the chapter refreshes", async () => {
    const initial = chapter();
    const updated = chapter("chapter-1", { version: 2, currentLessonIds: ["lesson-1"] });
    api.loadChapter.mockResolvedValueOnce(initial).mockResolvedValue(updated);
    api.generateChapterAi.mockResolvedValue(job("running"));
    api.loadChapterAiJob.mockResolvedValue(job("succeeded", { progress: 100, message: "已完成" }));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));
    await waitFor(() => expect(result.current.chapter?.chapterId).toBe("chapter-1"));
    await act(async () => { await result.current.startAiGeneration(); });

    await waitFor(() => expect(result.current.chapter?.currentLessonIds).toEqual(["lesson-1"]), { timeout: 3000 });
    expect(result.current.notice).toContain("AI 草稿已生成");
  });

  it("user Given the teacher reloads while an AI draft is running Then the stored chapter job restores its latest progress", async () => {
    api.loadChapter.mockResolvedValue(chapter("chapter-1", { generationJobId: "job-1" }));
    api.loadChapterAiJob.mockResolvedValue(job("running", { progress: 63, message: "正在检查来源引用" }));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));

    await waitFor(() => expect(result.current.aiJob).toMatchObject({ status: "running", progress: 63, message: "正在检查来源引用" }));
  });

  it("user Given another editor changed the chapter When AI generation reports a version conflict Then the latest chapter and a clear error are shown", async () => {
    const latest = chapter("chapter-1", { version: 2, recordVersion: 8 });
    api.loadChapter.mockResolvedValueOnce(chapter()).mockResolvedValue(latest);
    api.generateChapterAi.mockRejectedValue(new ApiRequestError("章节版本已变化", 409));
    const { result } = renderHook(() => useChapterStudio("chapter-1"));
    await waitFor(() => expect(result.current.chapter?.recordVersion).toBe(7));

    await act(async () => { await result.current.startAiGeneration(); });

    expect(result.current.chapter?.recordVersion).toBe(8);
    expect(result.current.error).toContain("章节已更新");
  });

  it("user Given a generation job is being restored When the teacher navigates to another chapter Then the old job response cannot replace the new chapter state", async () => {
    let finishOldJob: ((value: BackgroundJob) => void) | undefined;
    api.loadChapter.mockImplementation((chapterId: string) => Promise.resolve(chapter(
      chapterId, chapterId === "chapter-1" ? { generationJobId: "old-job" } : {},
    )));
    api.loadChapterAiJob.mockImplementation(() => new Promise<BackgroundJob>((resolve) => { finishOldJob = resolve; }));
    const { result, rerender } = renderHook(({ chapterId }: { chapterId: string }) => useChapterStudio(chapterId), { initialProps: { chapterId: "chapter-1" } });
    await waitFor(() => expect(finishOldJob).toBeTypeOf("function"));

    rerender({ chapterId: "chapter-2" });
    await waitFor(() => expect(result.current.chapter?.chapterId).toBe("chapter-2"));
    await act(async () => { finishOldJob?.(job("running", { jobId: "old-job" })); });

    expect(result.current.chapter?.chapterId).toBe("chapter-2");
    expect(result.current.aiJob).toBeNull();
  });
});
