// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProtectedAccess } from "./ProtectedAccess";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("protected access", () => {
  it("shows app directly in demo mode without asking for a session", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ protected: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    render(<ProtectedAccess><p>学生学习空间</p></ProtectedAccess>);
    expect(await screen.findByText("学生学习空间")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("lets a student exchange a teacher-issued invite for a server session", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ protected: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response("", { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ role: "student", learnerId: "student-a" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    render(<ProtectedAccess><p>学生学习空间</p></ProtectedAccess>);
    fireEvent.click(await screen.findByRole("button", { name: "学生" }));
    fireEvent.change(screen.getByLabelText("学生邀请令牌"), { target: { value: "one-time-invite" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("学生 student-a")).toBeTruthy();
    expect(await screen.findByText("学生学习空间")).toBeTruthy();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  });
});
