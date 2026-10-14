import type { components } from "../../types/generated/api";

type ApiPresentation = components["schemas"]["CoursewarePresentation"];
type ApiPublicDocument = components["schemas"]["CoursewarePublicDocument"];
type ApiPrivatePage = components["schemas"]["CoursewarePage"];
type ApiPublicPage = components["schemas"]["CoursewarePublicPage"];
type ApiBlock = NonNullable<ApiPrivatePage["blocks"]>[number] | NonNullable<ApiPublicPage["blocks"]>[number];
type ApiAsset = components["schemas"]["CoursewareAsset"];

export interface CoursewareSourceReference {
  page: number;
  sourceRevisionId: string;
}

export interface CoursewareAssetView {
  assetId: string;
  src?: string;
  alt: string;
  caption?: string;
  status: "available" | "missing" | "needs_review";
  sourceRefs: CoursewareSourceReference[];
  license?: string;
  attributionText?: string;
  distributionScope?: "private" | "classroom" | "public";
}

export type CoursewareBlockView =
  | { kind: "text"; blockId: string; text: string; sourceRefs: CoursewareSourceReference[] }
  | { kind: "image"; blockId: string; assetId: string; alt: string; caption?: string }
  | { kind: "formula"; blockId: string; latex: string; explanation?: string; sourceRefs: CoursewareSourceReference[] }
  | { kind: "word_cards"; blockId: string; cards: Array<{ term: string; meaning: string; example?: string; sourceRefs: CoursewareSourceReference[] }> }
  | { kind: "input"; blockId: string; prompt: string; minResponses: number; placeholder: string }
  | { kind: "checklist"; blockId: string; title: string; items: Array<{ itemId: string; label: string }> }
  | {
      kind: "activity";
      activityId: string;
      activityKind: string;
      instructions: string;
      assessmentMode: string;
      choices: string[];
      selectionMode?: "single" | "multiple";
      practiceReason?: string;
      answerKey?: string;
      acceptedAnswers?: string[];
      teacherRubric?: string;
    };

export interface CoursewareRevealView {
  revealId: string;
  label: string;
  content: string;
}

export interface CoursewarePageView {
  pageId: string;
  sectionId: string;
  pageKind: string;
  layout: string;
  title: string;
  objectiveIds: string[];
  sourceRefs: CoursewareSourceReference[];
  blocks: CoursewareBlockView[];
  teacherNotes?: string;
  revealSteps?: CoursewareRevealView[];
  estimatedMinutes: number;
}

export interface CoursewareSectionView {
  sectionId: string;
  title: string;
  purpose: string;
  estimatedMinutes: number;
  pageIds: string[];
}

export interface CoursewarePresentationDocument {
  coursewareId: string;
  publicationId: string;
  version: number;
  title: string;
  /** Opaque server-registered subject profile identifier. */
  subject: string;
  sourceUrl?: string | null;
  attributionText?: string | null;
  license?: string | null;
  distributionScope?: "private" | "classroom" | "public";
  durationMinutes: number;
  sourceTitle?: string;
  sections: CoursewareSectionView[];
  pages: CoursewarePageView[];
  assets: CoursewareAssetView[];
  canRevealAnswers: boolean;
}

/** Convert only at the player boundary; authoring always edits the full private API page contract. */
export function teacherPlaybackDocument(presentation: ApiPresentation): CoursewarePresentationDocument {
  const document = presentation.document;
  return toPlaybackDocument({
    coursewareId: presentation.coursewareId,
    publicationId: presentation.publicationId,
    version: presentation.version,
    title: document.title,
    subject: document.subject,
    sourceUrl: presentation.sourceUrl ?? document.sourceUrl,
    sourceTitle: presentation.sourceTitle ?? document.sourceTitle ?? undefined,
    attributionText: presentation.attributionText ?? document.attributionText,
    license: presentation.sourceLicense ?? document.sourceLicense,
    distributionScope: presentation.distributionScope,
    durationMinutes: document.durationMinutes,
    sections: document.sections ?? [],
    pages: document.pages ?? [],
    assets: document.assets ?? [],
  }, true);
}

/** Teacher-only draft preview reuses the player; it creates no publication or learner attempt. */
export function draftPlaybackDocument(document: components["schemas"]["CoursewareDocument"]): CoursewarePresentationDocument {
  return toPlaybackDocument({
    coursewareId: document.coursewareId, publicationId: `draft:${document.coursewareId}`,
    version: document.recordVersion, title: document.title, subject: document.subject,
    sourceUrl: document.sourceUrl, sourceTitle: document.sourceTitle ?? undefined,
    attributionText: document.attributionText, license: document.sourceLicense,
    distributionScope: "private", durationMinutes: document.durationMinutes,
    sections: document.sections ?? [], pages: document.pages ?? [], assets: document.assets ?? [],
  }, true);
}

/** Student playback is built only from the safe published projection; it never falls back to teacher data. */
export function studentPlaybackDocument(document: ApiPublicDocument): CoursewarePresentationDocument {
  return toPlaybackDocument({
    coursewareId: document.coursewareId,
    publicationId: document.publicationId,
    version: document.version,
    title: document.title,
    subject: document.subject,
    sourceUrl: document.sourceUrl,
    sourceTitle: document.sourceTitle ?? undefined,
    attributionText: document.attributionText,
    license: document.sourceLicense,
    distributionScope: document.sourceDistributionScope,
    durationMinutes: document.durationMinutes,
    sections: document.sections,
    pages: document.pages,
    assets: document.assets,
  }, false);
}

function toPlaybackDocument(input: {
  coursewareId: string;
  publicationId: string;
  version: number;
  title: string;
  subject: string;
  sourceUrl?: string | null;
  sourceTitle?: string;
  attributionText?: string | null;
  license?: string | null;
  distributionScope?: "private" | "classroom" | "public";
  durationMinutes: number;
  sections: components["schemas"]["CoursewareSection"][];
  pages: Array<ApiPrivatePage | ApiPublicPage>;
  assets: ApiAsset[];
}, canRevealAnswers: boolean): CoursewarePresentationDocument {
  return {
    ...input,
    sections: input.sections.map((section) => ({ ...section, pageIds: section.pageIds ?? [] })),
    pages: input.pages.map((page) => ({
      pageId: page.pageId,
      sectionId: page.sectionId,
      pageKind: page.pageKind,
      layout: page.layout,
      title: page.title,
      objectiveIds: page.objectiveIds ?? [],
      sourceRefs: sourceRefs(page.sourceRefs),
      blocks: (page.blocks ?? []).map((block) => blockView(block, canRevealAnswers)),
      ...(canRevealAnswers && "teacherNotes" in page ? { teacherNotes: page.teacherNotes } : {}),
      ...(canRevealAnswers && "revealSteps" in page ? {
        revealSteps: (page.revealSteps ?? []).map(({ revealId, label, content }) => ({ revealId, label, content })),
      } : {}),
      estimatedMinutes: page.estimatedMinutes,
    })),
    assets: input.assets.map(assetView),
    canRevealAnswers,
  };
}

function sourceRefs(refs: components["schemas"]["CoursewareSourceRef"][] | undefined): CoursewareSourceReference[] {
  return (refs ?? []).map(({ page, sourceRevisionId }) => ({ page, sourceRevisionId }));
}

function blockView(block: ApiBlock, canRevealAnswers: boolean): CoursewareBlockView {
  switch (block.kind) {
    case "text":
      return { kind: "text", blockId: block.blockId, text: block.text, sourceRefs: sourceRefs(block.sourceRefs) };
    case "image":
      return { kind: "image", blockId: block.blockId, assetId: block.assetId, alt: block.alt, caption: block.caption ?? undefined };
    case "formula":
      return { kind: "formula", blockId: block.blockId, latex: block.latex, explanation: block.explanation ?? undefined, sourceRefs: sourceRefs(block.sourceRefs) };
    case "word_cards":
      return { kind: "word_cards", blockId: block.blockId, cards: block.cards.map((card) => ({ ...card, example: card.example ?? undefined, sourceRefs: sourceRefs(card.sourceRefs) })) };
    case "input":
      return { kind: "input", blockId: block.blockId, prompt: block.prompt, minResponses: block.minResponses, placeholder: block.placeholder };
    case "checklist":
      return { kind: "checklist", blockId: block.blockId, title: block.title, items: block.items.map((item) => ({ ...item })) };
    case "activity":
      return {
        kind: "activity",
        activityId: block.activityId,
        activityKind: block.activityKind,
        instructions: block.instructions,
        assessmentMode: block.assessmentMode,
        choices: block.choices ?? [],
        selectionMode: block.selectionMode ?? "single",
        practiceReason: block.practiceReason ?? undefined,
        ...(canRevealAnswers && "answerKey" in block && block.answerKey != null ? { answerKey: Array.isArray(block.answerKey) ? block.answerKey.join("\n") : block.answerKey } : {}),
        ...(canRevealAnswers && "acceptedAnswers" in block && block.acceptedAnswers ? { acceptedAnswers: block.acceptedAnswers } : {}),
        ...(canRevealAnswers && "teacherRubric" in block && block.teacherRubric ? { teacherRubric: block.teacherRubric } : {}),
      };
  }
}

function assetView(asset: ApiAsset): CoursewareAssetView {
  const src = asset.src?.startsWith("/api/courseware/") ? asset.src : undefined;
  return {
    assetId: asset.assetId,
    ...(src ? { src } : {}),
    alt: asset.alt,
    status: asset.status,
    sourceRefs: sourceRefs(asset.sourceRefs),
    license: asset.license ?? undefined,
    attributionText: asset.attributionText ?? undefined,
    distributionScope: asset.distributionScope,
  };
}
