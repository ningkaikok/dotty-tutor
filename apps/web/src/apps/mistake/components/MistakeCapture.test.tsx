// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetLearnerIdCacheForTests, setCurrentLearnerId } from "../../../api/identity";
import { MistakeCapture, mistakeImportJobStorageKey } from "./MistakeCapture";

const {
  queueMistakeImport,
  cancelMistakeImportJob,
  retryMistakeImportJob,
  loadMistakeImportJob,
} = vi.hoisted(() => ({
  queueMistakeImport: vi.fn(),
  cancelMistakeImportJob: vi.fn(),
  retryMistakeImportJob: vi.fn(),
  loadMistakeImportJob: vi.fn(),
}));

vi.mock("../../../api/mistakes", () => ({
  queueMistakeImport,
  cancelMistakeImportJob,
  retryMistakeImportJob,
  loadMistakeImportJob,
}));

vi.mock("./ImageCropper", () => ({
  ImageCropper: () => <div role="region" aria-label="裁切错题图片" />,
  cropImageFile: vi.fn(async (file: File) => file),
}));

const job = (status: "queued" | "failed" | "cancelled") => ({
  jobId: "job-1",
  captureId: "capture-1",
  jobType: "mistake.image.import",
  status,
  progress: status === "failed" ? 100 : 0,
  message: status === "failed" ? "识别失败" : "等待 Worker 处理",
  attemptCount: status === "failed" ? 1 : 0,
  maxAttempts: 3,
  cancelRequested: false,
  lastError: null,
  result: null,
  createdAt: 1,
  updatedAt: 1,
  startedAt: null,
  completedAt: null,
});

function chooseImage() {
  const input = screen.getByLabelText("选择错题图片");
  fireEvent.change(input, {
    target: { files: [new File(["image"], "mistake.png", { type: "image/png", lastModified: 1 })] },
  });
}

describe("MistakeCapture job controls", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.localStorage.clear();
    resetLearnerIdCacheForTests();
  });

  it("invokes cancellation and preserves the capture id across a retried request", async () => {
    const user = userEvent.setup();
    queueMistakeImport
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce(job("queued"))
      .mockResolvedValueOnce({ ...job("queued"), jobId: "job-2", captureId: "capture-2" });
    cancelMistakeImportJob.mockResolvedValue(job("cancelled"));
    const onCreated = vi.fn();
    render(<MistakeCapture onCreated={onCreated} />);

    chooseImage();
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("response lost"));
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "取消识别" })).toBeVisible());

    expect(queueMistakeImport).toHaveBeenNthCalledWith(
      2,
      expect.any(File),
      expect.any(String),
      expect.objectContaining({ sourceText: "", originalAnswer: "" }),
    );
    expect(queueMistakeImport.mock.calls[0][1]).toBe(queueMistakeImport.mock.calls[1][1]);

    await user.click(screen.getByRole("button", { name: "取消识别" }));
    await waitFor(() => expect(cancelMistakeImportJob).toHaveBeenCalledWith("job-1"));
    expect(onCreated).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(queueMistakeImport).toHaveBeenCalledTimes(3));
    expect(queueMistakeImport.mock.calls[1][1]).toBe(queueMistakeImport.mock.calls[0][1]);
    expect(queueMistakeImport.mock.calls[2][1]).not.toBe(queueMistakeImport.mock.calls[1][1]);
  });

  it("invokes a bounded retry action for failed jobs", async () => {
    const user = userEvent.setup();
    queueMistakeImport.mockResolvedValue(job("failed"));
    retryMistakeImportJob.mockResolvedValue(job("queued"));
    render(<MistakeCapture onCreated={vi.fn()} />);

    chooseImage();
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /重试识别/ })).toBeVisible());
    await user.click(screen.getByRole("button", { name: /重试识别/ }));

    await waitFor(() => expect(retryMistakeImportJob).toHaveBeenCalledWith("job-1"));
    expect(screen.getByRole("status")).toHaveTextContent("等待 Worker 处理");
  });

  it("ignores the legacy global key and does not restore another learner's job after switching identity", async () => {
    setCurrentLearnerId("stu-001");
    window.localStorage.setItem("dotty:mistake-import-job", JSON.stringify({
      jobId: "legacy-job",
      captureId: "legacy-capture",
    }));
    window.localStorage.setItem(mistakeImportJobStorageKey("stu-001"), JSON.stringify({
      learnerId: "stu-001",
      jobId: "stu-1-job",
      captureId: "stu-1-capture",
    }));
    loadMistakeImportJob.mockResolvedValue(job("queued"));
    render(<MistakeCapture onCreated={vi.fn()} />);

    await waitFor(() => expect(loadMistakeImportJob).toHaveBeenCalledWith("stu-1-job"));
    expect(loadMistakeImportJob).not.toHaveBeenCalledWith("legacy-job");
    expect(await screen.findByRole("button", { name: "取消识别" })).toBeVisible();

    setCurrentLearnerId("stu-002");
    await waitFor(() => expect(screen.queryByRole("button", { name: "取消识别" })).not.toBeInTheDocument());
    expect(loadMistakeImportJob).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-001"))).toBeTruthy();
    expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-002"))).toBeNull();
  });

  it("drops a restored job whose completed result belongs to another learner", async () => {
    setCurrentLearnerId("stu-001");
    window.localStorage.setItem(mistakeImportJobStorageKey("stu-001"), JSON.stringify({
      learnerId: "stu-001",
      jobId: "wrong-result-job",
      captureId: "capture-1",
    }));
    loadMistakeImportJob.mockResolvedValue({
      ...job("failed"),
      status: "succeeded",
      result: { learnerId: "stu-002" },
    });
    render(<MistakeCapture onCreated={vi.fn()} />);

    await waitFor(() => expect(loadMistakeImportJob).toHaveBeenCalledWith("wrong-result-job"));
    await waitFor(() => expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-001"))).toBeNull());
    expect(screen.queryByRole("button", { name: "取消识别" })).not.toBeInTheDocument();
  });

  it("ignores a pending submit response after the learner changes", async () => {
    const user = userEvent.setup();
    let resolveQueue: ((value: ReturnType<typeof job>) => void) | undefined;
    setCurrentLearnerId("stu-001");
    queueMistakeImport.mockReturnValue(new Promise((resolve) => { resolveQueue = resolve; }));
    render(<MistakeCapture onCreated={vi.fn()} />);

    chooseImage();
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(queueMistakeImport).toHaveBeenCalledTimes(1));

    setCurrentLearnerId("stu-002");
    resolveQueue?.(job("queued"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "取消识别" })).not.toBeInTheDocument());
    expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-002"))).toBeNull();
  });

  it("ignores a pending cancellation response after the learner changes", async () => {
    const user = userEvent.setup();
    let resolveCancel: ((value: ReturnType<typeof job>) => void) | undefined;
    setCurrentLearnerId("stu-001");
    queueMistakeImport.mockResolvedValue(job("queued"));
    cancelMistakeImportJob.mockReturnValue(new Promise((resolve) => { resolveCancel = resolve; }));
    const onCreated = vi.fn();
    render(<MistakeCapture onCreated={onCreated} />);

    chooseImage();
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "取消识别" })).toBeVisible());
    await user.click(screen.getByRole("button", { name: "取消识别" }));
    await waitFor(() => expect(cancelMistakeImportJob).toHaveBeenCalledWith("job-1"));

    setCurrentLearnerId("stu-002");
    resolveCancel?.(job("cancelled"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "取消识别" })).not.toBeInTheDocument());
    expect(onCreated).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-002"))).toBeNull();
  });

  it("ignores a pending retry response after the learner changes", async () => {
    const user = userEvent.setup();
    let resolveRetry: ((value: ReturnType<typeof job>) => void) | undefined;
    setCurrentLearnerId("stu-001");
    queueMistakeImport.mockResolvedValue(job("failed"));
    retryMistakeImportJob.mockReturnValue(new Promise((resolve) => { resolveRetry = resolve; }));
    const onCreated = vi.fn();
    render(<MistakeCapture onCreated={onCreated} />);

    chooseImage();
    await user.click(screen.getByRole("button", { name: "加入识别队列" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /重试识别/ })).toBeVisible());
    await user.click(screen.getByRole("button", { name: /重试识别/ }));
    await waitFor(() => expect(retryMistakeImportJob).toHaveBeenCalledWith("job-1"));

    setCurrentLearnerId("stu-002");
    resolveRetry?.(job("queued"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /重试识别/ })).not.toBeInTheDocument());
    expect(onCreated).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(mistakeImportJobStorageKey("stu-002"))).toBeNull();
  });

  it("clears the previous learner's selected image and text after switching identity", async () => {
    const user = userEvent.setup();
    setCurrentLearnerId("stu-001");
    render(<MistakeCapture onCreated={vi.fn()} />);

    chooseImage();
    await user.type(screen.getByPlaceholderText("例如：我算出 x = 2，或上传照片后在这里补充步骤"), "x = 2");
    await user.type(screen.getByPlaceholderText("可以直接粘贴题干；留空时由 OCR 自动识别"), "题目");
    setCurrentLearnerId("stu-002");

    await waitFor(() => expect(screen.getByRole("button", { name: /拍照或选择图片/ })).toBeVisible());
    expect(screen.getByPlaceholderText("例如：我算出 x = 2，或上传照片后在这里补充步骤")).toHaveValue("");
    expect(screen.getByPlaceholderText("可以直接粘贴题干；留空时由 OCR 自动识别")).toHaveValue("");
  });
});
