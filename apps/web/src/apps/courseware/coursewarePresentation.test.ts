import { describe, expect, it } from "vitest";
import { draftPlaybackDocument, studentPlaybackDocument, teacherPlaybackDocument } from "./coursewarePresentation";
import type { components } from "../../types/generated/api";

const sourceRef = { sourceRevisionId: "revision-1", page: 23, sentenceId: "sentence-1", quote: "source quote" };
const privateDocument = {
  coursewareId: "course-1", chapterId: "chapter-1", planVersion: 1, subject: "english", title: "Source lesson", audience: "Grade 8", languagePolicy: "English", durationMinutes: 40,
  pageStart: 23, pageEnd: 25, recordVersion: 1, status: "published", generationStatus: "completed", sourceReadiness: "ready", reviewStatus: "ready", sourceDistributionScope: "classroom", sourceRevisionId: "revision-1", createdAt: 1, updatedAt: 1,
  sections: [{ sectionId: "section-1", title: "Opening", purpose: "Start", estimatedMinutes: 4, pageIds: ["page-1"] }],
  pages: [{ pageId: "page-1", sectionId: "section-1", pageKind: "activity", layout: "two_column", title: "Discuss", objectiveIds: ["goal-1"], sourceRefs: [sourceRef], estimatedMinutes: 4, generationStatus: "completed", reviewStatus: "approved", version: 1,
    teacherNotes: "Private teacher notes.", revealSteps: [{ revealId: "reveal-1", label: "Model", content: "Private model answer.", reviewStatus: "approved", sourceRefs: [sourceRef] }],
    blocks: [{ kind: "activity", blockId: "block-1", activityId: "activity-1", activityKind: "choice", instructions: "Choose an answer", assessmentMode: "objective", authorship: "teacher_authored", choices: ["A", "B"], answerKey: "A", acceptedAnswers: ["A"], teacherRubric: "Teacher rubric", objectiveIds: ["goal-1"], sourceRefs: [sourceRef] }],
  }],
  assets: [{ assetId: "asset-local", kind: "image", src: "/api/courseware/course-1/assets/asset-local?publicationId=pub-1", alt: "Source image", status: "available", sourceRefs: [sourceRef], license: "CC BY", distributionScope: "classroom" }, { assetId: "asset-external", kind: "image", src: "https://untrusted.example/image.png", alt: "Untrusted", status: "available", sourceRefs: [] }],
  reviewIssues: [],
} as unknown as components["schemas"]["CoursewareDocument"];

const publicDocument = {
  coursewareId: "course-1", publicationId: "pub-1", version: 1, title: "Source lesson", subject: "english", durationMinutes: 40, status: "published", sourceTitle: "Selected source", sourceUrl: "https://example.test/source", sourceLicense: "CC BY", attributionText: "Author credit", sourceDistributionScope: "classroom",
  sections: [{ sectionId: "section-1", title: "Opening", purpose: "Start", estimatedMinutes: 4, pageIds: ["page-1"] }],
  pages: [{ pageId: "page-1", sectionId: "section-1", pageKind: "activity", layout: "two_column", title: "Discuss", objectiveIds: ["goal-1"], sourceRefs: [sourceRef], estimatedMinutes: 4,
    blocks: [{ kind: "activity", blockId: "block-1", activityId: "activity-1", activityKind: "choice", instructions: "Choose an answer", assessmentMode: "objective", authorship: "teacher_authored", choices: ["A", "B"], objectiveIds: ["goal-1"], sourceRefs: [sourceRef] }],
  }],
  assets: [{ assetId: "asset-local", kind: "image", src: "/api/courseware/course-1/assets/asset-local?publicationId=pub-1", alt: "Source image", status: "available", sourceRefs: [sourceRef], license: "CC BY", distributionScope: "classroom" }],
} as unknown as components["schemas"]["CoursewarePublicDocument"];

describe("courseware player projections", () => {
  it("user previews a pending tutorial privately without creating a student publication", () => {
    const source = { ...privateDocument, status: "in_review" as const, publicationId: null, reviewStatus: "pending" as const };
    const preview = draftPlaybackDocument(source);
    expect(preview.publicationId).toBe("draft:course-1");
    expect(preview.distributionScope).toBe("private");
    expect(preview.canRevealAnswers).toBe(true);
    expect(preview.pages[0].teacherNotes).toBe("Private teacher notes.");
    expect(source.status).toBe("in_review");
    expect(source.publicationId).toBeNull();
  });
  it("keeps teacher notes, reveal steps, and answer guidance out of student playback while retaining verified source assets", () => {
    const teacher = teacherPlaybackDocument({ coursewareId: "course-1", publicationId: "pub-1", version: 1, distributionScope: "classroom", document: privateDocument } as components["schemas"]["CoursewarePresentation"]);
    const student = studentPlaybackDocument(publicDocument);

    expect(teacher.canRevealAnswers).toBe(true);
    expect(teacher.pages[0].teacherNotes).toBe("Private teacher notes.");
    expect(teacher.pages[0].revealSteps?.[0].content).toBe("Private model answer.");
    expect(teacher.pages[0].blocks[0]).toMatchObject({ answerKey: "A", acceptedAnswers: ["A"], teacherRubric: "Teacher rubric" });
    expect(student.canRevealAnswers).toBe(false);
    expect(student.pages[0]).not.toHaveProperty("teacherNotes");
    expect(student.pages[0]).not.toHaveProperty("revealSteps");
    expect(student.pages[0].blocks[0]).not.toHaveProperty("answerKey");
    expect(student.pages[0].blocks[0]).not.toHaveProperty("acceptedAnswers");
    expect(student.pages[0].blocks[0]).not.toHaveProperty("teacherRubric");
    expect(teacher.pages[0].sourceRefs).toEqual([{ sourceRevisionId: "revision-1", page: 23 }]);
    expect(student.assets).toHaveLength(1);
    expect(student.assets[0].src).toBe("/api/courseware/course-1/assets/asset-local?publicationId=pub-1");
  });
});
