import { useEffect, useRef, useState } from "react";
import { confirmMistake } from "../../../api/mistakes";
import { QuestionAnswer } from "../../../components/QuestionAnswer";
import MathText from "../../../MathText";
import { displayedPrompt } from "../../../questionPresentation";
import type { MistakeErrorReason, MistakeItem, TutorStage } from "../../../types/index";
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

// 与旧确认页完全一致的六个错因选项和描述文案：错因归因从确认页迁移到陪练
// 首轮，这里是它唯一的落脚点，文案本身不应该在迁移过程中被改写。
const ERROR_REASONS: Array<[MistakeErrorReason, string, string]> = [
  ["concept", "概念不清", "定义、公式或原理没有理解"],
  ["reading", "审题错误", "遗漏或误解了题目条件"],
  ["calculation", "计算失误", "方法正确但运算出错"],
  ["missing_step", "步骤遗漏", "推导、证明或单位不完整"],
  ["unknown", "完全不会", "不知道从哪里开始"],
  ["careless", "粗心大意", "会做但抄错、看错或没检查"],
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

  // 只有错题确认时没有留下错因，才需要在陪练首轮补一次自评；已经有值时
  // 这个步骤完全不出现，行为和迁移前一致。跳过时保持 resolved=true 但不
  // 写回任何值——“跳过”和“unknown（完全不会）”是两种不同的学生状态，
  // 混在一起会污染后续出题策略。
  const [assessmentResolved, setAssessmentResolved] = useState(Boolean(item.errorReason));
  const [selectedReason, setSelectedReason] = useState<MistakeErrorReason | "">("");
  const [savingReason, setSavingReason] = useState(false);
  const [reasonError, setReasonError] = useState("");

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

  const submitSelfAssessment = async (reason: MistakeErrorReason) => {
    if (savingReason) return;
    setSavingReason(true);
    setReasonError("");
    try {
      // PATCH 是整体确认接口，必须带上现有字段一并写回，否则会把题干、
      // 分类等已确认内容清空。题干取值方式与确认页保持一致，避免把图片
      // Markdown 写回 prompt。
      await confirmMistake(item.mistakeId, {
        prompt: displayedPrompt(question),
        originalAnswer: item.originalAnswer,
        subject: item.subject,
        gradeBand: item.gradeBand,
        chapter: item.chapter,
        knowledgePoint: item.knowledgePoint,
        notes: item.notes,
        errorReason: reason,
      });
      setAssessmentResolved(true);
    } catch (requestError) {
      setReasonError(requestError instanceof Error ? requestError.message : "保存失败，请重试或直接跳过");
    } finally {
      setSavingReason(false);
    }
  };

  const skipSelfAssessment = () => {
    if (savingReason) return;
    setReasonError("");
    setAssessmentResolved(true);
  };

  return (
    <section className="tutor-layout">
      <aside className="tutor-question-card">
        <span className="eyebrow">原题</span>
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
          readOnly={!understanding || !assessmentResolved}
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

        {!assessmentResolved ? (
          <div className="tutor-self-assessment">
            <div className="tutor-message assistant">
              <strong>Dotty</strong>
              <p>先说说你觉得错在哪？先自己判断，再看 Dotty 的分析——这一步本身就是训练。</p>
            </div>
            <fieldset className="error-reason-fieldset">
              <legend>这次为什么做错？</legend>
              <div>
                {ERROR_REASONS.map(([value, label, description]) => (
                  <label key={value} className={selectedReason === value ? "selected" : ""}>
                    <input
                      type="radio"
                      name="tutorErrorReason"
                      value={value}
                      checked={selectedReason === value}
                      disabled={savingReason}
                      onChange={() => setSelectedReason(value)}
                    />
                    <span><strong>{label}</strong><small>{description}</small></span>
                  </label>
                ))}
              </div>
            </fieldset>
            {reasonError && <p className="mistake-error" role="alert">{reasonError}</p>}
            <div className="tutor-actions">
              <button
                className="mistake-primary-action compact"
                disabled={!selectedReason || savingReason}
                onClick={() => selectedReason && void submitSelfAssessment(selectedReason)}
              >
                {savingReason ? "正在保存…" : "确定，开始陪练"}
              </button>
              <button disabled={savingReason} onClick={skipSelfAssessment}>我说不好，直接开始</button>
            </div>
          </div>
        ) : (
          <>
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
          </>
        )}
      </div>
    </section>
  );
}
