import { useSyncExternalStore } from "react";
import { DEMO_LEARNER_ID } from "./client";

/**
 * Demo 模式下保存本机学生选择；protected 模式缓存服务端会话里的 learnerId 供 UI 组装请求。
 * 此模块本身从不授权：HttpOnly 服务端会话是身份事实，API 必须核对所有传入 learnerId。
 */
const STORAGE_KEY = "dotty-learner-id";
const AUTH_SESSION_KEY = "dotty-auth-session";
export interface ProtectedSession {
  role: "teacher" | "student";
  learnerId: string | null;
  expiresAt: number;
}

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

export function protectedSession(): ProtectedSession | null {
  try {
    const raw = localStorage.getItem(AUTH_SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as ProtectedSession;
    return value && (value.role === "teacher" || value.role === "student") ? value : null;
  } catch {
    return null;
  }
}

export function setProtectedSession(session: ProtectedSession | null): void {
  try {
    if (session) localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(session));
    else localStorage.removeItem(AUTH_SESSION_KEY);
  } catch {
    // The HttpOnly cookie remains the authority; this copy only drives the UI.
  }
  if (session?.role === "student" && session.learnerId) current = session.learnerId;
  listeners.forEach((listener) => listener());
}

/** 当前身份。所有需要 learnerId 的调用点都应当读它，不要再引用 DEMO_LEARNER_ID。 */
export function currentLearnerId(): string {
  const session = protectedSession();
  if (session?.role === "student" && session.learnerId) return session.learnerId;
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
