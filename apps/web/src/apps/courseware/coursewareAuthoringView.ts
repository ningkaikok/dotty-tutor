import type { components } from "../../types/generated/api";

/** Authoring keeps the complete private server contract so edits retain provenance and stable IDs. */
type ApiDocument = components["schemas"]["CoursewareDocument"];
type ApiPage = components["schemas"]["CoursewarePage"];
type ApiPageContent = components["schemas"]["CoursewarePageContent"];
export type CoursewareAuthoringPage = Omit<ApiPage, "blocks" | "objectiveIds" | "sourceRefs" | "revealSteps"> & {
  blocks: NonNullable<ApiPage["blocks"]>;
  objectiveIds: NonNullable<ApiPage["objectiveIds"]>;
  sourceRefs: NonNullable<ApiPage["sourceRefs"]>;
  revealSteps: NonNullable<ApiPage["revealSteps"]>;
};
export type CoursewareAuthoringDocument = Omit<ApiDocument, "pages" | "sections" | "assets" | "reviewIssues"> & {
  pages: CoursewareAuthoringPage[];
  sections: Array<Omit<NonNullable<ApiDocument["sections"]>[number], "pageIds"> & { pageIds: string[] }>;
  assets: NonNullable<ApiDocument["assets"]>;
  reviewIssues: NonNullable<ApiDocument["reviewIssues"]>;
};
export type CoursewarePageEditView = Omit<ApiPageContent, "blocks" | "objectiveIds" | "sourceRefs" | "revealSteps"> & {
  blocks: NonNullable<ApiPageContent["blocks"]>;
  objectiveIds: NonNullable<ApiPageContent["objectiveIds"]>;
  sourceRefs: NonNullable<ApiPageContent["sourceRefs"]>;
  revealSteps: NonNullable<ApiPageContent["revealSteps"]>;
};
export type CoursewareEditableBlock = NonNullable<CoursewarePageEditView["blocks"]>[number];

/** Normalize sparse optional arrays at the API edge without dropping the full private page DTO. */
export function authoringDocument(document: ApiDocument): CoursewareAuthoringDocument {
  return {
    ...document,
    pages: (document.pages ?? []).map((page) => ({
      ...page,
      blocks: page.blocks ?? [],
      objectiveIds: page.objectiveIds ?? [],
      sourceRefs: page.sourceRefs ?? [],
      revealSteps: page.revealSteps ?? [],
    })),
    sections: (document.sections ?? []).map((section) => ({ ...section, pageIds: section.pageIds ?? [] })),
    assets: document.assets ?? [],
    reviewIssues: document.reviewIssues ?? [],
  };
}

export function editablePage(page: CoursewareAuthoringPage): CoursewarePageEditView {
  return {
    title: page.title,
    pageKind: page.pageKind,
    layout: page.layout,
    objectiveIds: [...page.objectiveIds],
    sourceRefs: structuredClone(page.sourceRefs),
    blocks: structuredClone(page.blocks),
    teacherNotes: page.teacherNotes,
    revealSteps: page.revealSteps.map(({ revealId, label, content, sourceRefs }) => ({
      revealId,
      label,
      content,
      sourceRefs: structuredClone(sourceRefs ?? []),
    })),
    estimatedMinutes: page.estimatedMinutes,
  };
}
