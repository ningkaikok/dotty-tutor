import type { GeneratedSuccess } from "./client";
import { parse } from "./client";

type DependencyPreflightResponse = GeneratedSuccess<"dependency_preflight_api_system_dependency_preflight_get">;

export interface DependencyCheck {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
  optional: boolean;
}

export interface DependencyPreflightReport {
  ok: boolean;
  checks: DependencyCheck[];
}

export async function loadDependencyPreflight(): Promise<DependencyPreflightReport> {
  const response = await fetch("/api/system/dependency-preflight", { cache: "no-store" });
  const payload = await parse<DependencyPreflightResponse>(response);
  return payload as unknown as DependencyPreflightReport;
}
