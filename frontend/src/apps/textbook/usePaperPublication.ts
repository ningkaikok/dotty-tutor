import { useState } from "react";
import { createPublication, saveLesson, updatePublicationStatus } from "../../api";
import { lessonDocumentFromPayload } from "../../lesson/lessonDocument";
import type { PublicationSummary, QuestionPayload, TextbookImportResult } from "../../types";

/**
 * Coordinates the explicit draft → review → publish workflow.
 * Generation stays separate: a successfully generated question bank is not
 * student-visible until the content producer performs these actions.
 */
export function usePaperPublication(
  textbookImport: TextbookImportResult | null,
  questionBank: QuestionPayload[],
) {
  const [publication, setPublication] = useState<PublicationSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submitForReview = async () => {
    if (!textbookImport || !questionBank.length || busy) return;
    setBusy(true);
    setError("");
    try {
      await Promise.all(questionBank.map((item) =>
        saveLesson(lessonDocumentFromPayload(item, textbookImport.uploadId)),
      ));
      const created = await createPublication({
        title: `${textbookImport.extraction.chapter || textbookImport.filename} · 互动试卷`,
        sourceUploadId: textbookImport.uploadId,
        lessonIds: questionBank.map((item) => item.question.id),
      });
      setPublication(await updatePublicationStatus(created.publicationId, "in_review"));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "试卷送审失败");
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!publication || busy) return;
    setBusy(true);
    setError("");
    try {
      setPublication(await updatePublicationStatus(publication.publicationId, "published"));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "试卷发布失败");
    } finally {
      setBusy(false);
    }
  };

  return {
    publication,
    publicationBusy: busy,
    publicationError: error,
    submitForReview,
    publish,
    resetPublication: () => {
      setPublication(null);
      setError("");
    },
  };
}
