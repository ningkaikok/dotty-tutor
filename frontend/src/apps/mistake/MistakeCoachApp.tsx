interface MistakeCoachAppProps {
  onExit: () => void;
}

const FLOW = [
  ["拍照录题", "提取题干、公式、题图和学生原答案"],
  ["确认归类", "匹配教材章节、知识点和错误原因"],
  ["单题陪练", "按学生每次输入动态选择提示和追问"],
  ["验证掌握", "连续答对不同变式后进入进阶本与复习计划"],
] as const;

export function MistakeCoachApp({ onExit }: MistakeCoachAppProps) {
  return (
    <main className="mistake-shell">
      <header className="mistake-header">
        <button className="route-back-button" onClick={onExit}>← 全部功能</button>
        <span className="phase-badge">PHASE 01</span>
      </header>

      <section className="mistake-hero">
        <span className="eyebrow">AI MISTAKE COACH</span>
        <h1>AI 错题陪练</h1>
        <p>不是只收藏答案，而是围绕一道错题持续诊断、提示、练习，直到验证真正学会。</p>
        <div className="mistake-scope">首个可用版本聚焦初中数学；当前阶段已建立独立产品入口和架构边界。</div>
      </section>

      <section className="mistake-flow" aria-label="错题陪练流程">
        {FLOW.map(([title, description], index) => (
          <article key={title}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <h2>{title}</h2>
              <p>{description}</p>
            </div>
          </article>
        ))}
      </section>

      <section className="mistake-next-step">
        <div>
          <span className="eyebrow">NEXT</span>
          <h2>下一阶段：错题拍照与确认</h2>
          <p>将复用现有 OCR、题目结构、判题和 PostgreSQL 能力，增加学生可修正的错题确认页。</p>
        </div>
        <button disabled>即将开放</button>
      </section>
    </main>
  );
}
