import { useSyncExternalStore } from "react";
import { DEMO_LEARNER_ID } from "./client";

/**
 * 当前学习者身份（**不是**登录，只是本机上的一个选择）。
 *
 * 背景：老师在班级花名册里加的 learnerId 与学生端使用的 learnerId 必须是同一个值，
 * 两边才能对上——`class_memberships.learner_id` 和学生侧所有表用的是同一列。此前
 * 学生端把 `local-demo` 写死在调用点里，导致老师加的任何学生都收不到作业，看板上
 * 永远显示"未开始"。这个模块把那个值变成可切换的。
 *
 * 边界必须说清楚：**任何人都可以声称自己是任意一个 learnerId**，本模块不做也不能做
 * 任何校验。它只服务于"一台机器、老师在场"的试用场景。接入服务端登录后，本模块连同
 * 学生端的身份选择器一起删除，调用点改为使用服务端下发的身份。
 */
const STORAGE_KEY = "dotty-learner-id";

const listeners = new Set<() => void>();
/**
 * 缓存当前值，让 useSyncExternalStore 的 getSnapshot 保持引用稳定。
 * 每次都去读 localStorage 也能拿到正确的值，但 React 会因为 getSnapshot
 * 在同一次渲染里返回不同结果而报错（隐私模式下抛异常时尤其明显）。
 */
let current: string | null = null;

function readStored(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored && stored.trim() ? stored : DEMO_LEARNER_ID;
  } catch {
    // 隐私模式或站点数据被禁用时 localStorage 访问直接抛异常。此时退回默认身份，
    // 学生仍然可以正常做题，只是换不了人。
    return DEMO_LEARNER_ID;
  }
}

/** 当前身份。所有需要 learnerId 的调用点都应当读它，不要再引用 DEMO_LEARNER_ID。 */
export function currentLearnerId(): string {
  if (current === null) current = readStored();
  return current;
}

/** 切换身份并广播。传空值等于恢复默认身份。 */
export function setCurrentLearnerId(learnerId: string): void {
  const next = learnerId.trim() || DEMO_LEARNER_ID;
  if (next === currentLearnerId()) return;
  current = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // 存不下就只在本次会话内生效；不能因此让切换失败。
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 订阅当前身份；切换后依赖它的组件会重新取数。 */
export function useLearnerId(): string {
  return useSyncExternalStore(subscribe, currentLearnerId, () => DEMO_LEARNER_ID);
}

/** 仅供测试重置模块级缓存，避免用例之间互相污染。 */
export function resetLearnerIdCacheForTests(): void {
  current = null;
}
