import { parse } from "./client";
import type { components } from "../types/generated/api";

export type PromptDetail = components["schemas"]["PromptDetail"];
export type PromptRevision = components["schemas"]["PromptRevision"];
export type PromptList = components["schemas"]["PromptList"];

/** 内容平台凭据只由页面内存传入，不写 URL 或浏览器持久化存储。 */
export async function promptRequest<T>(token: string, path = "", body?: unknown): Promise<T> {
  return parse<T>(await fetch(`/api/content/prompts${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Content-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  }));
}
