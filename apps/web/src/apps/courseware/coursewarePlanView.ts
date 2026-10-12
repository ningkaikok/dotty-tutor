import type { components } from "../../types/generated/api";

type ApiPlan = components["schemas"]["CoursewarePlan"];
type ApiPlanEdit = components["schemas"]["CoursewarePlanEdit"];
type ApiSession = components["schemas"]["CoursewareSessionPlan"];
type ApiSection = components["schemas"]["CoursewareSectionPlan"];
type ApiPage = components["schemas"]["CoursewarePageOutline"];

export type CoursewarePlanPageView = Omit<ApiPage, "objectiveIds"> & { objectiveIds: string[] };
export type CoursewarePlanSectionView = Omit<ApiSection, "pages"> & { pages: CoursewarePlanPageView[] };
export type CoursewarePlanSessionView = Omit<ApiSession, "sections"> & { sections: CoursewarePlanSectionView[] };
export type CoursewarePlanView = Omit<ApiPlan, "objectives" | "sessions"> & {
  objectives: NonNullable<ApiPlan["objectives"]>;
  sessions: CoursewarePlanSessionView[];
};
export type CoursewarePlanPageKind = ApiPage["pageKind"];
export type CoursewarePlanEditView = Omit<ApiPlanEdit, "expectedRecordVersion" | "sessions"> & {
  sessions: CoursewarePlanSessionView[];
};

/** Normalize sparse optional arrays at the API edge while retaining every server-owned outline field. */
export function planView(plan: ApiPlan): CoursewarePlanView {
  const sessions: CoursewarePlanSessionView[] = (plan.sessions ?? []).map((session) => ({
    ...session,
    sections: (session.sections ?? []).map((section) => ({
      ...section,
      pages: (section.pages ?? []).map((page) => ({ ...page, objectiveIds: page.objectiveIds ?? [] })),
    })),
  }));
  return { ...plan, objectives: plan.objectives ?? [], sessions };
}

export function editablePlan(plan: CoursewarePlanView): CoursewarePlanEditView {
  return {
    audience: plan.audience,
    languagePolicy: plan.languagePolicy,
    pageStart: plan.pageStart,
    pageEnd: plan.pageEnd,
    objectives: structuredClone(plan.objectives),
    sessions: structuredClone(plan.sessions),
  };
}
