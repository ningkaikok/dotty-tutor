// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useCoursewareAiReview } from "./useCoursewareAiReview";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function status(version: number) {
  return { coursewareId: "cw", recordVersion: version, samplingRate: 0.1, pagesToReview: 0,
    countsAsHumanReview: false, pages: [], metrics: { compared: 0, agreements: 0, missed: 0, falseAlarms: 0 } };
}

it("user Given an AI verdict When editing the document Then the earlier result is hidden until the current version loads", async () => {
  let resolveCurrent!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json(status(3)))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveCurrent = resolve; })));
  const { result, rerender } = renderHook(({ version }) => useCoursewareAiReview("cw", version), { initialProps: { version: 3 } });
  await waitFor(() => expect(result.current.status?.recordVersion).toBe(3));
  rerender({ version: 4 });
  expect(result.current.status).toBeNull();
  await act(async () => { resolveCurrent(Response.json(status(4))); });
  await waitFor(() => expect(result.current.status?.recordVersion).toBe(4));
});

it("user Given a slow previous review request When a new version is loaded Then a late old response cannot replace the current result", async () => {
  let resolveOld!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
    .mockResolvedValueOnce(Response.json(status(4))));
  const { result, rerender } = renderHook(({ version }) => useCoursewareAiReview("cw", version), { initialProps: { version: 3 } });
  rerender({ version: 4 });
  await waitFor(() => expect(result.current.status?.recordVersion).toBe(4));
  await act(async () => { resolveOld(Response.json(status(3))); });
  expect(result.current.status?.recordVersion).toBe(4);
});
