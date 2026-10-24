// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as wholeBookApi from "../../api/wholeBook";
import type { WholeBookRunView, WholeBookStartInput } from "../../api/wholeBook";
import { useWholeBookRun } from "./useWholeBookRun";

afterEach(() => vi.restoreAllMocks());

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function run(uploadId: string): WholeBookRunView {
  return {
    runId: `run-${uploadId}`,
    uploadId,
    filename: `${uploadId}.pdf`,
    status: "queued",
    message: "等待开始",
    totalPages: 30,
    completedPages: 0,
    completedBatches: 0,
    batchCount: 1,
    currentBatchIndex: 0,
    batches: [],
    canPause: true,
    canResume: false,
    createdAt: 1,
    updatedAt: 1,
  };
}

it("user Given requests for an old textbook are still pending When the selected upload changes Then late progress and start responses cannot replace the new textbook", async () => {
  const oldRead = deferred<WholeBookRunView>();
  const oldStart = deferred<WholeBookRunView>();
  vi.spyOn(wholeBookApi, "loadWholeBookForUpload").mockImplementation((uploadId) =>
    uploadId === "old-book" ? oldRead.promise : Promise.resolve(run(uploadId)));
  vi.spyOn(wholeBookApi, "startWholeBook").mockReturnValue(oldStart.promise);

  const { result, rerender } = renderHook(({ uploadId }) => useWholeBookRun(uploadId), {
    initialProps: { uploadId: "old-book" },
  });
  const startInput: WholeBookStartInput = { mode: "whole_book", subject: "science", distributionScope: "private" };
  let oldStartRequest!: ReturnType<typeof result.current.start>;
  act(() => { oldStartRequest = result.current.start(startInput); });

  rerender({ uploadId: "new-book" });
  await waitFor(() => expect(result.current.run?.uploadId).toBe("new-book"));

  await act(async () => {
    oldRead.resolve(run("old-book"));
    oldStart.resolve(run("old-book"));
    await Promise.all([oldStartRequest]);
  });

  expect(result.current.run?.uploadId).toBe("new-book");
});
