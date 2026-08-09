import { useEffect, useState } from "react";
import {
  createPublication,
  createPublicationRevision,
  loadPublicationWorkspace,
  saveLesson,
  updatePublicationStatus,
} from "../../api";
import { lessonDocumentFromPayload } from "../../lesson/lessonDocument";
import type { PublicationSummary, QuestionPayload, TextbookImportResult } from "../../types";

/**
 * 协调明确的 draft → in_review → published 发布状态流。
 *
 * “生成成功”与“学生可见”是两个不同事实：题库必须经过内容生产者送审和发布操作才能进入学生端。
 * 新版采用不可变 ID，重新审核不会覆盖旧版课程或已产生的学习记录。
 */
export function usePaperPublication(
  textbookImport: TextbookImportResult | null,
  questionBank: QuestionPayload[],
) {
  const [publication, setPublication] = useState<PublicationSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoredQuestionBank, setRestoredQuestionBank] = useState<QuestionPayload[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    // 路由状态在刷新后会丢失，因此按 sourceUploadId 从数据库恢复最新试卷及其题目快照。
    // active 标记防止用户快速切换教材时，较慢的旧请求覆盖新教材页面。
    const uploadId = textbookImport?.uploadId;
    if (!uploadId) return;
    let active = true;
    setRestoring(true);
    void loadPublicationWorkspace(uploadId)
      .then((workspace) => {
        if (!active) return;
        setPublication(workspace.publication);
        setRestoredQuestionBank(workspace.questionPayloads.length ? workspace.questionPayloads : null);
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "试卷版本恢复失败");
        }
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => {
      active = false;
    };
  }, [textbookImport?.uploadId]);

  const submitForReview = async () => {
    if (!textbookImport || !questionBank.length || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
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
    setNotice("");
    try {
      const published = await updatePublicationStatus(publication.publicationId, "published");
      setPublication(published);
      if (published.qualityRecovery) {
        setNotice(
          `已自动隔离 ${published.qualityRecovery.quarantinedCount} 道异常题，`
          + `其余 ${published.qualityRecovery.publishedCount} 道题已安全发布。`,
        );
      } else {
        setNotice("试卷已发布，全部题目均通过自动质量校验。");
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "试卷发布失败");
    } finally {
      setBusy(false);
    }
  };

  const regenerateRevision = async (): Promise<QuestionPayload[] | null> => {
    if (!publication || busy) return null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await createPublicationRevision(publication.publicationId);
      setPublication(result.publication);
      setRestoredQuestionBank(result.questionPayloads);
      setNotice(
        `已保留旧版并生成 v${result.publication.version}；`
        + `共 ${result.questionPayloads.length} 道题等待发布。`,
      );
      return result.questionPayloads;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "整套重新审核失败");
      return null;
    } finally {
      setBusy(false);
    }
  };

  return {
    publication,
    publicationBusy: busy || restoring,
    publicationError: error,
    publicationNotice: notice,
    restoredQuestionBank,
    submitForReview,
    publish,
    regenerateRevision,
    resetPublication: () => {
      setPublication(null);
      setRestoredQuestionBank(null);
      setError("");
      setNotice("");
    },
  };
}
