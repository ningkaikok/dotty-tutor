// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAssignmentPlan } from "../../api/classroom";
import type { AssignmentPlan } from "../../types/classroom";
import { useAssignmentPlanning } from "./useAssignmentPlanning";

vi.mock("../../api/classroom", () => ({
  createAssignmentPlan: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const plan = (classId: string) => ({ classId, planId: `plan-${classId}` }) as AssignmentPlan;

afterEach(() => vi.resetAllMocks());

describe("assignment planning request scope", () => {
  it("user sees the selected class plan when an older class response arrives later", async () => {
    const oldRequest = deferred<AssignmentPlan>();
    const currentRequest = deferred<AssignmentPlan>();
    vi.mocked(createAssignmentPlan)
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(currentRequest.promise);
    const { result, rerender } = renderHook(({ classId }) => useAssignmentPlanning(classId), {
      initialProps: { classId: "class-a" },
    });

    let oldResponse!: Promise<AssignmentPlan | null>;
    act(() => { oldResponse = result.current.analyze("paper-a"); });
    rerender({ classId: "class-b" });
    let currentResponse!: Promise<AssignmentPlan | null>;
    act(() => { currentResponse = result.current.analyze("paper-b"); });
    await act(async () => { currentRequest.resolve(plan("class-b")); await currentResponse; });
    await act(async () => { oldRequest.resolve(plan("class-a")); await oldResponse; });

    expect(result.current.plan).toEqual(plan("class-b"));
    expect(result.current.error).toBe("");
    expect(result.current.planning).toBe(false);
  });
});
