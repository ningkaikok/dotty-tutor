import { useEffect, useMemo, useState } from "react";
import { playSpeech, preloadSpeech, stopSpeech } from "../speech";
import MathText from "../MathText";
import type { CanvasAction, LessonBlock, QuestionPayload } from "../types";
import { lessonDocumentFromPayload } from "./lessonDocument";
import { renderLessonBlock } from "./rendererRegistry";

interface LessonPlayerProps {
  payload: QuestionPayload;
  onActionChange?: (action: CanvasAction) => void;
  studentMode?: boolean;
}

const ignoreCanvasAction = () => undefined;

export function LessonPlayer({ payload, onActionChange = ignoreCanvasAction, studentMode = false }: LessonPlayerProps) {
  const document = useMemo(() => lessonDocumentFromPayload(payload), [payload]);
  const playableBlocks = useMemo(
    () => document.blocks.filter((block) => block.type !== "quiz"),
    [document],
  );
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const current = playableBlocks[step];

  const activateBlock = (block: LessonBlock | undefined) => {
    if (block?.type === "diagram") onActionChange(block.payload.action);
  };

  const blockNarration = (block: LessonBlock) => {
    if (block.type === "diagram") return block.payload.speechText;
    if (block.type === "markdown") return block.payload.markdown;
    if (block.type === "formula") return `${block.title}，${block.payload.latex}`;
    if (block.type === "animation") return block.payload.caption || block.title;
    if (block.type === "annotation") return block.payload.text;
    if (block.type === "hint") return block.payload.hint;
    return block.title;
  };

  useEffect(() => {
    stopSpeech();
    setStep(0);
    setPlaying(false);
    activateBlock(playableBlocks[0]);
    // 只预热首步。一次性请求整节课的所有旁白会把 Qwen3-TTS 串行队列塞满，切题时
    // 旧请求也来不及释放；下一步在真正播放前再按需请求，并由 stopSpeech 统一取消。
    const firstBlock = playableBlocks[0];
    if (firstBlock) void preloadSpeech(blockNarration(firstBlock));

    return stopSpeech;
  }, [document.lessonId]);

  useEffect(() => {
    if (!playing || !current) return;
    let cancelled = false;
    const narration = blockNarration(current);

    void (async () => {
      // 先确认音频可播放，再改变画布动作，避免“动画先走完、声音才出现”。
      await preloadSpeech(narration);
      if (cancelled) return;
      await playSpeech(narration, () => {
        if (!cancelled) activateBlock(current);
      });
      if (cancelled) return;
      if (step >= playableBlocks.length - 1) setPlaying(false);
      else setStep((value) => value + 1);
    })();

    return () => {
      cancelled = true;
      stopSpeech();
    };
  }, [current, onActionChange, playableBlocks.length, playing, step]);

  if (!current) return null;

  return (
    <section className="hero-grid" aria-label={document.title}>
      <article className="canvas-card panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{payload.question.chapter}</span>
            <h1>{document.title}</h1>
          </div>
          <button
            className="lesson-button"
            onClick={() => {
              if (!playing && step >= playableBlocks.length - 1) setStep(0);
              setPlaying((value) => !value);
            }}
          >{playing ? "暂停讲解" : "播放讲解"}</button>
        </div>
        {renderLessonBlock(current, document.title)}
        <div className="lesson-progress" aria-label="讲解步骤">
          {playableBlocks.map((block, index) => (
            <button
              key={block.id}
              className={index === step ? "active" : ""}
              onClick={() => {
                stopSpeech();
                setStep(index);
                activateBlock(block);
                setPlaying(false);
                void preloadSpeech(blockNarration(block));
              }}
              aria-label={`切换到步骤 ${index + 1}`}
              aria-current={index === step ? "step" : undefined}
            />
          ))}
        </div>
      </article>
      <aside className="explanation-card panel" aria-live="polite">
        <span className="eyebrow">STEP {step + 1} / {playableBlocks.length}</span>
        <h2>{current.title}</h2>
        <p><MathText text={blockNarration(current)} /></p>
        {!studentMode && (
          <>
            <div className="speech-copy"><span>speechText</span><MathText text={blockNarration(current)} /></div>
            <div className="flow-row"><span>Renderer</span><b>+</b><span>Content Block</span><b>+</b><span>TTS</span></div>
          </>
        )}
      </aside>
    </section>
  );
}
