// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentLearnerId, resetLearnerIdCacheForTests } from "../../api/identity";
import { StudentIdentityPicker } from "./StudentIdentityPicker";

const ROSTER = [
  { learnerId: "stu-001", displayName: "小安", classId: "class-1", className: "初二数学一班" },
  { learnerId: "stu-002", displayName: "小林", classId: "class-1", className: "初二数学一班" },
];

/**
 * 显式注入内存版 localStorage。
 *
 * 不能依赖测试环境提供它：当前 Node 26 + jsdom 组合下 `window.localStorage` 是
 * undefined（Node 自己的实验性实现需要 --localstorage-file，jsdom 的也没暴露出来）。
 * 注入一份既让持久化断言可验证，也顺带证明模块只使用了 getItem/setItem 两个方法。
 */
function stubStorage(): Storage {
  const map = new Map<string, string>();
  const storage = {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
    clear: () => map.clear(),
    key: (index: number) => [...map.keys()][index] ?? null,
    get length() {
      return map.size;
    },
  } as Storage;
  vi.stubGlobal("localStorage", storage);
  return storage;
}

function stubRoster(items: unknown[] | Error) {
  vi.stubGlobal("fetch", vi.fn(async () => {
    if (items instanceof Error) return { ok: false, json: async () => ({ message: items.message }) };
    return { ok: true, json: async () => ({ items }) };
  }));
}

describe("StudentIdentityPicker", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = stubStorage();
    resetLearnerIdCacheForTests();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    resetLearnerIdCacheForTests();
  });

  it("选择同学后切换当前身份并持久化", async () => {
    stubRoster(ROSTER);
    render(<StudentIdentityPicker />);
    const select = await screen.findByRole("combobox");

    await userEvent.selectOptions(select, "stu-002");

    expect(currentLearnerId()).toBe("stu-002");
    expect(storage.getItem("dotty-learner-id")).toBe("stu-002");
  });

  it("默认身份不在花名册时仍然列出，避免显示的人和实际请求的人不一致", async () => {
    stubRoster(ROSTER);
    render(<StudentIdentityPicker />);

    // 默认身份是 local-demo，不是花名册里的任何一个学生。
    expect(await screen.findByRole("combobox")).toHaveValue("local-demo");
    expect(screen.getByRole("option", { name: /不在花名册中/ })).toBeInTheDocument();
  });

  it("没有班级时不渲染任何东西，纯 Demo 场景保持原样", async () => {
    stubRoster([]);
    const { container } = render(<StudentIdentityPicker />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("花名册读取失败不打断做题，只是不显示选择器", async () => {
    stubRoster(new Error("后端不可用"));
    const { container } = render(<StudentIdentityPicker />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(currentLearnerId()).toBe("local-demo");
  });

  it("localStorage 不可用时仍然可以切换，只是不跨会话保留", async () => {
    // 隐私模式、站点数据被禁用，或本测试环境这种根本没有 localStorage 的情况。
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("storage disabled");
      },
      setItem: () => {
        throw new Error("storage disabled");
      },
    } as unknown as Storage);
    resetLearnerIdCacheForTests();
    stubRoster(ROSTER);
    render(<StudentIdentityPicker />);

    await userEvent.selectOptions(await screen.findByRole("combobox"), "stu-001");

    expect(currentLearnerId()).toBe("stu-001");
  });
});
