import { useEffect, useRef } from "react";
import { QuestionAnswer } from "../../../components/QuestionAnswer";
import MathText from "../../../MathText";
import type { MistakeItem, TutorStage } from "../../../types";
import { useMistakeTutor } from "../useMistakeTutor";
import { VariationPractice } from "./VariationPractice";

interface MistakeTutorProps {
  item: MistakeItem;
}

const STAGES: Array<{ id: TutorStage; label: string }> = [
  { id: "diagnose", label: "定位卡点" },
  { id: "explain", label: "解释误区" },
  { id: "practice", label: "引导练习" },
  { id: "verify", label: "准备验证" },
];

export function MistakeTutor({ item }: MistakeTutorProps) {
  const state = useMistakeTutor(item);
  const messagesEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state.thread?.messageCount]);

  if (state.loading) return <div className="mistake-empty">正在恢复这道题的辅导上下文…</div>;
  if (!state.thread) return <p className="mistake-error" role="alert">{state.error || "无法创建辅导线程"}</p>;

  const question = item.questionPayload.question;
  const activeStage = STAGES.findIndex((stage) => stage.id === state.thread?.stage);
  const hasSubmittedTurn = Boolean(state.thread.messages?.some((message) => message.role === "student"));

  return (
    <section className="tutor-layout">
      <aside className="tutor-question-card">
        <span className="eyebrow">ORIGINAL MISTAKE</span>
        <div className="tutor-question-meta">
          <span>{item.chapter}</span><span>{item.knowledgePoint}</span>
        </div>
        {item.originalAnswer && <p><strong>原来的答案：</strong><MathText text={item.originalAnswer} /></p>}
        <QuestionAnswer
          question={question}
          selectedOptions={state.selectedOptions}
          blankAnswers={state.blankAnswers}
          numericAnswer={state.numericAnswer}
          drawConnections={state.drawConnections}
          onSelectOption={state.selectOption}
          onBlankChange={(id, value) => state.setBlankAnswers((current) => ({ ...current, [id]: value }))}
          onNumericChange={state.setNumericAnswer}
          onDrawConnectionsChange={state.setDrawConnections}
        />
      </aside>

      <div className="tutor-chat-card">
        <ol className="tutor-stage-list" aria-label="辅导阶段">
          {STAGES.map((stage, index) => (
            <li key={stage.id} className={index < activeStage ? "complete" : index === activeStage ? "active" : "pending"}>
              <span>{index < activeStage ? "✓" : index + 1}</span>{stage.label}
            </li>
          ))}
        </ol>

        <div className="tutor-messages" aria-live="polite">
          {!state.thread.messages?.length && (
            <div className="tutor-message assistant">
              <strong>Dotty</strong>
              <p>我们不急着看答案。你当时做到哪一步开始不确定？也可以直接重新作答。</p>
            </div>
          )}
          {state.thread.messages?.map((message) => (
            <div key={message.messageId} className={`tutor-message ${message.role}`}>
              <strong>{message.role === "student" ? "我" : "Dotty"}</strong>
              <p><MathText text={message.content} /></p>
              {message.assessment && (
                <small className={`assessment ${message.assessment}`}>
                  {message.assessment === "correct" ? "本轮正确" : message.assessment === "incorrect" ? "需要修正" : "继续思考"}
                </small>
              )}
            </div>
          ))}
          <div ref={messagesEnd} />
        </div>

        <label className="tutor-input">
          <span>继续回答或描述你的想法</span>
          <textarea
            value={state.studentInput}
            onChange={(event) => state.setStudentInput(event.target.value)}
            placeholder="例如：我觉得要先比较这些数和 1 的大小……"
            disabled={state.sending}
          />
        </label>
        {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        <div className="tutor-actions">
          <button disabled={state.sending} onClick={() => void state.submit("help")}>给我一点提示</button>
          <button className="mistake-primary-action compact" disabled={state.sending} onClick={() => void state.submit("answer")}>
            {state.sending ? "正在思考…" : hasSubmittedTurn ? "重新提交" : "提交这一轮"}
          </button>
        </div>
        <small className="tutor-context-note">仅保存结构化状态、摘要和必要消息；不会无限重放全部对话。</small>
        {state.thread.stage === "verify" && <VariationPractice mistakeId={item.mistakeId} autoStart />}
      </div>
    </section>
  );
}
