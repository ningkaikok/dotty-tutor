import { parse } from "./client";
import type {
  AssignmentSummary,
  ClassDashboard,
  ClassDetail,
  ClassSummary,
  StudentAssignment,
} from "../types/classroom";

export async function loadClasses(): Promise<ClassSummary[]> {
  const payload = await parse<{ items: ClassSummary[] }>(await fetch("/api/classes", { cache: "no-store" }));
  return payload.items;
}

export async function loadClass(classId: string): Promise<ClassDetail> {
  return parse<ClassDetail>(await fetch(`/api/classes/${encodeURIComponent(classId)}`, { cache: "no-store" }));
}

export async function createClass(input: { name: string; subject?: string; gradeBand?: string }): Promise<ClassDetail> {
  return parse<ClassDetail>(await fetch("/api/classes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function addClassMember(classId: string, input: { learnerId: string; displayName: string }): Promise<void> {
  await parse(await fetch(`/api/classes/${encodeURIComponent(classId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function createAssignment(
  classId: string,
  input: { publicationId: string; title?: string; dueAt?: number | null },
): Promise<AssignmentSummary> {
  return parse<AssignmentSummary>(await fetch(`/api/classes/${encodeURIComponent(classId)}/assignments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function loadStudentAssignments(learnerId: string): Promise<StudentAssignment[]> {
  const payload = await parse<{ learnerId: string; items: StudentAssignment[] }>(
    await fetch(`/api/assignments?learnerId=${encodeURIComponent(learnerId)}`, { cache: "no-store" }),
  );
  return payload.items;
}

export async function loadClassDashboard(classId: string, assignmentId?: string): Promise<ClassDashboard> {
  const query = assignmentId ? `?assignmentId=${encodeURIComponent(assignmentId)}` : "";
  return parse<ClassDashboard>(await fetch(`/api/classes/${encodeURIComponent(classId)}/dashboard${query}`, { cache: "no-store" }));
}
