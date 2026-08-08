import { useEffect, useMemo, useState } from "react";
import { playSpeech, preloadSpeech, stopSpeech } from "../speech";
import type { CanvasAction, LessonBlock, QuestionPayload } from "../types";
import { lessonDocumentFromPayload } from "./lessonDocument";
import { renderLessonBlock } from "./rendererRegistry";

interface LessonPlayerProps {
  payload: QuestionPayload;
  onActionChange: (action: CanvasAction) => void;
}

export function LessonPlayer({ payload, onActionChange }: LessonPlayerProps) {
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
    // Local TTS synthesis can take longer than a single step's playback, so queue
    // every step's narration as soon as the lesson loads instead of just the next one.
    // requestSpeech deduplicates these calls, so eager preloading does not create
    // duplicate network requests when the learner clicks a step manually.
    for (const block of playableBlocks) void preloadSpeech(blockNarration(block));

    return stopSpeech;
  }, [document.lessonId]);

  useEffect(() => {
    if (!playing || !current) return;
    let cancelled = false;
    const narration = blockNarration(current);
    const next = playableBlocks[step + 1];

    void (async () => {
      // Fetch before changing the canvas, then keep the next narration warm
      // while the current audio is playing.
      await preloadSpeech(narration);
      if (cancelled) return;
      if (next) void preloadSpeech(blockNarration(next));
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
        <p>{blockNarration(current)}</p>
        <div className="speech-copy"><span>speechText</span>{blockNarration(current)}</div>
        <div className="flow-row"><span>Renderer</span><b>+</b><span>Content Block</span><b>+</b><span>TTS</span></div>
      </aside>
    </section>
  );
}
