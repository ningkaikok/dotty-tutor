import { parse } from "./client";
import { currentLearnerId } from "./identity";
import type { components } from "../types/generated/api";

// FastAPI includes default-valued fields in responses. Require them locally
// while retaining the generated schema as the single contract definition.
export type CoursewareReadingPosition = Required<components["schemas"]["CoursewareReadingPosition"]>;
export type CoursewareUnitLesson = Required<Omit<components["schemas"]["CoursewareUnitLesson"], "progress">> & {
  progress: CoursewareReadingPosition | null;
};
export type CoursewareUnit = Omit<components["schemas"]["CoursewareUnit"], "lessons" | "resume"> & {
  lessons: CoursewareUnitLesson[];
  resume: CoursewareReadingPosition | null;
};

const learnerQuery = (learnerId = currentLearnerId()) => `learnerId=${encodeURIComponent(learnerId)}`;

export async function listCoursewareUnits(learnerId: string, signal?: AbortSignal) {
  return parse<{ items: CoursewareUnit[] }>(await fetch(`/api/courseware-units?${learnerQuery(learnerId)}`, { cache: "no-store", signal }));
}

export async function loadCoursewareUnit(coursewareId: string, role: "teacher" | "student", learnerId: string, signal?: AbortSignal) {
  return parse<CoursewareUnit>(await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/${role === "teacher" ? "teaching-unit" : "unit"}?${learnerQuery(learnerId)}`, { cache: "no-store", signal }));
}

export async function loadChapterCoursewareUnit(chapterId: string, signal?: AbortSignal) {
  return parse<CoursewareUnit>(await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware-unit`, { cache: "no-store", signal }));
}

export async function saveCoursewarePosition(coursewareId: string, input: components["schemas"]["CoursewarePositionSave"], learnerId: string) {
  return parse<CoursewareReadingPosition>(await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/progress?${learnerQuery(learnerId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }));
}
