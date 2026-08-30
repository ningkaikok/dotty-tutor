import { useState } from "react";
import { QuestionAnswer } from "../../../components/QuestionAnswer";
import { RichText } from "../../../RichText";
import type { ReviewTask, StructuredAnswerInput } from "../../../types/index";
import { buildStructuredAnswer } from "../structuredAnswer";

interface ReviewTaskCardProps {
  task: ReviewTask;
  serverTime: number;
  busy: boolean;
  onStart: (taskId: string) => Promise<void>;
  onAnswer: (taskId: string, answer: StructuredAnswerInput) => Promise<void>;
}

export function ReviewTaskCard({ task, serverTime, busy, onStart, onAnswer }: ReviewTaskCardProps) {
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [localError, setLocalError] = useState("");
  const isDue = task.dueAt <= serverTime;
  const dueLabel = new Date(task.dueAt * 1000).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  const question = task.questionPayload?.question;

  const selectOption = (label: string) => {
    if (!question) return;
    const multiple = question.questionType === "multi-select" || question.selectionMode === "multiple";
    setSelectedOptions((current) => multiple
      ? current.includes(label) ? current.filter((item) => item !== label) : [...current, label]
      : [label]);
  };

  const submit = async () => {
    if (!question) return;
    const answer = buildStructuredAnswer(question, selectedOptions, blankAnswers, numericAnswer);
    if (!answer.content.trim()) {
      setLocalError("请先输入或选择答案");
      return;
    }
    setLocalError("");
    await onAnswer(task.taskId, answer);
  };

  return (
    <article className={`review-task-card ${task.status}`}>
      <header>
        <div>
          <strong>第 {task.intervalDays} 天复习 · {task.mistake?.knowledgePoint || "知识点巩固"}</strong>
          <span>{task.mistake?.chapter ? `${task.mistake.chapter} · ` : ""}{dueLabel}</span>
        </div>
        <span className={isDue ? "review-due" : "review-upcoming"}>
          {task.status === "completed" ? "已完成" : isDue ? "今日待复习" : "即将开始"}
        </span>
      </header>
      {task.status === "scheduled" && (
        <button className="mistake-primary-action compact" disabled={busy} onClick={() => void onStart(task.taskId)}>
          {busy ? "正在生成…" : isDue ? "开始复习" : "提前复习"}
        </button>
      )}
      {task.status === "ready" && question && (
        <>
          <div className="review-question">
            <QuestionAnswer
              question={question}
              selectedOptions={selectedOptions}
              blankAnswers={blankAnswers}
              numericAnswer={numericAnswer}
              drawConnections={[]}
              onSelectOption={(label) => selectOption(label)}
              onBlankChange={(id, value) => setBlankAnswers((current) => ({ ...current, [id]: value }))}
              onNumericChange={setNumericAnswer}
              onDrawConnectionsChange={() => undefined}
            />
          </div>
          {localError && <p className="mistake-error">{localError}</p>}
          <button className="mistake-primary-action compact" disabled={busy} onClick={() => void submit()}>
            {busy ? "正在判定…" : "提交复习答案"}
          </button>
        </>
      )}
      {task.status === "completed" && (
        <div className={`review-result ${task.assessment}`}>
          <strong>{task.assessment === "correct" ? "复习正确" : "本次仍需巩固"}</strong>
          <p><RichText text={task.feedback} /></p>
        </div>
      )}
    </article>
  );
}
