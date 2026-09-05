import { parse } from "./client";
import type {
  AssignmentSummary,
  AssignmentPlan,
  ClassDashboard,
  ClassDetail,
  ClassSummary,
  RosterEntry,
  StudentAssignment,
} from "../types/classroom";

/**
 * 班级花名册，供学生端"我是谁"选择器读取。
 *
 * 这是名册查询、不是身份认证：返回全部学生的 learnerId 与姓名，任何人都能声称自己
 * 是其中任意一个。接入登录后应连同学生端选择器一起删除。
 */
export async function loadRoster(): Promise<RosterEntry[]> {
  const payload = await parse<{ items: RosterEntry[] }>(await fetch("/api/learners", { cache: "no-store" }));
  return payload.items;
}

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
  input: { planId: string; publicationId: string; title?: string; dueAt?: number | null; sourceFingerprint: string; confirmWarnings: boolean },
): Promise<AssignmentSummary> {
  return parse<AssignmentSummary>(await fetch(`/api/classes/${encodeURIComponent(classId)}/assignments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function createAssignmentPlan(classId: string, publicationId: string): Promise<AssignmentPlan> {
  return parse<AssignmentPlan>(await fetch(`/api/classes/${encodeURIComponent(classId)}/assignment-plans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ publicationId }),
  }));
}

export async function loadAssignmentPlan(classId: string, planId: string): Promise<AssignmentPlan> {
  return parse<AssignmentPlan>(await fetch(`/api/classes/${encodeURIComponent(classId)}/assignment-plans/${encodeURIComponent(planId)}`, { cache: "no-store" }));
}

export async function createPersonalizedAssignment(
  classId: string,
  planId: string,
  questionCount: number,
): Promise<AssignmentPlan> {
  return parse<AssignmentPlan>(await fetch(
    `/api/classes/${encodeURIComponent(classId)}/assignment-plans/${encodeURIComponent(planId)}/personalized`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questionCount }),
    },
  ));
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

export async function recordTeacherReview(
  classId: string,
  assignmentId: string,
  input: {
    learnerId: string;
    questionId?: string;
    knowledgePointId?: string;
    action: "reviewed" | "overturned" | "mastery_override";
    masteryScore?: number;
    correctedAssessment?: "correct" | "partial" | "incorrect";
    note?: string;
  },
): Promise<void> {
  await parse(await fetch(
    `/api/classes/${encodeURIComponent(classId)}/assignments/${encodeURIComponent(assignmentId)}/reviews`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  ));
}
