import { useEffect, useRef } from "react";
import { QuestionAnswer } from "../../../components/QuestionAnswer";
import { RichText } from "../../../RichText";
import type { MistakeItem, TutorStage } from "../../../types/index";
import { useMistakeTutor } from "../useMistakeTutor";
import { VariationPractice } from "./VariationPractice";

interface MistakeTutorProps {
  item: MistakeItem;
}

const STAGES: Array<{ id: "understanding" | "practice" | "verify"; label: string }> = [
  { id: "understanding", label: "理解错因" },
  { id: "practice", label: "变式练习" },
  { id: "verify", label: "掌握验证" },
];

function visibleStage(stage: TutorStage): (typeof STAGES)[number]["id"] {
  return stage === "diagnose" || stage === "explain" ? "understanding" : stage;
}

function assessmentLabel(message: { assessment?: string; action: Record<string, unknown> }) {
  if (message.assessment === "incorrect") return "需要修正";
  if (message.assessment !== "correct") return "继续思考";
  const plan = message.action.tutorTurnPlan as { audit?: { assessmentAuthority?: string } } | undefined;
  return plan?.audit?.assessmentAuthority === "deterministic" ? "答案正确" : "理解方向正确";
}

export function MistakeTutor({ item }: MistakeTutorProps) {
  const state = useMistakeTutor(item);
  const messagesEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state.thread?.messageCount]);

  if (state.loading) return <div className="mistake-empty">正在恢复这道题的辅导上下文…</div>;
  if (!state.thread) return <p className="mistake-error" role="alert">{state.error || "无法创建辅导线程"}</p>;

  const thread = state.thread;
  const question = item.questionPayload.question;
  const activeStage = STAGES.findIndex((stage) => stage.id === visibleStage(thread.stage));
  const understanding = visibleStage(thread.stage) === "understanding";
  const hasSubmittedTurn = Boolean(thread.messages?.some((message) => message.role === "student"));

  return (
    <section className="tutor-layout">
      <aside className="tutor-question-card">
        <span className="eyebrow">ORIGINAL MISTAKE</span>
        <div className="tutor-question-meta">
          <span>{item.chapter}</span><span>{item.knowledgePoint}</span>
        </div>
        {item.originalAnswer && <p><strong>原来的答案：</strong><RichText text={item.originalAnswer} /></p>}
        <QuestionAnswer
          question={question}
          selectedOptions={state.selectedOptions}
          blankAnswers={state.blankAnswers}
          numericAnswer={state.numericAnswer}
          drawConnections={state.drawConnections}
          subQuestionAnswers={state.subQuestionAnswers}
          onSelectOption={state.selectOption}
          onBlankChange={(id, value) => state.setBlankAnswers((current) => ({ ...current, [id]: value }))}
          onNumericChange={state.setNumericAnswer}
          onDrawConnectionsChange={state.setDrawConnections}
          onSubQuestionChange={(id, answer) => state.setSubQuestionAnswers((current) => ({ ...current, [id]: answer }))}
          readOnly={!understanding}
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
              <p><RichText text={message.content} /> </p>
              {message.assessment && (
                <small className={`assessment ${message.assessment}`}>
                  {assessmentLabel(message)}
                </small>
              )}
            </div>
          ))}
          <div ref={messagesEnd} />
        </div>

        {understanding && <label className="tutor-input">
          <span>继续回答或描述你的想法</span>
          <textarea
            value={state.studentInput}
            onChange={(event) => state.setStudentInput(event.target.value)}
            placeholder="例如：我觉得要先比较这些数和 1 的大小……"
            disabled={state.sending}
          />
        </label>}
        {understanding && state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        {understanding && <div className="tutor-actions">
          <button disabled={state.sending} onClick={() => void state.submit("help")}>给我一点提示</button>
          <button className="mistake-primary-action compact" disabled={state.sending} onClick={() => void state.submit("answer")}>
            {state.sending ? "正在思考…" : hasSubmittedTurn ? "重新提交" : "提交这一轮"}
          </button>
        </div>}
        {understanding && <small className="tutor-context-note">仅保存结构化状态、摘要和必要消息；不会无限重放全部对话。</small>}
        {(thread.stage === "practice" || thread.stage === "verify") && (
          <VariationPractice
            mistakeId={item.mistakeId}
            autoStart
            onStageChange={state.setStage}
          />
        )}
      </div>
    </section>
  );
}
