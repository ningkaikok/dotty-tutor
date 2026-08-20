import { GeometryCanvas } from "../GeometryCanvas";
import MathText from "../MathText";
import type { LessonBlock } from "../types/index";

interface LessonRendererProps {
  block: LessonBlock;
  topic: string;
}

type LessonBlockRenderer = (props: LessonRendererProps) => React.ReactNode;

// LessonBlock 是判别联合。注册表让播放器只负责步骤与播放状态，而不依赖每一种内容块的实现。
// 新增内容类型时只需补充契约、Renderer 和一条注册记录，不必继续扩大 LessonPlayer 的条件分支。
const renderers: Record<LessonBlock["type"], LessonBlockRenderer> = {
  markdown: ({ block }) => block.type === "markdown"
    ? <MathText block text={block.payload.markdown} className="lesson-markdown" />
    : null,
  formula: ({ block }) => block.type === "formula"
    ? <MathText block text={`$$${block.payload.latex}$$`} className="lesson-formula" />
    : null,
  diagram: ({ block, topic }) => block.type === "diagram"
    ? (
      <GeometryCanvas
        action={block.payload.action}
        topic={topic}
        title={block.title}
        text={block.payload.text}
      />
    )
    : null,
  animation: ({ block }) => block.type === "animation"
    ? (
      <figure className="lesson-animation">
        <video controls preload="metadata" src={block.payload.src} poster={block.payload.poster} />
        {block.payload.caption && <figcaption>{block.payload.caption}</figcaption>}
      </figure>
    )
    : null,
  annotation: ({ block }) => block.type === "annotation"
    ? <aside className="lesson-annotation"><MathText text={block.payload.text} /></aside>
    : null,
  quiz: () => null,
  hint: ({ block }) => block.type === "hint"
    ? <aside className="lesson-hint"><MathText text={block.payload.hint} /></aside>
    : null,
};

export function renderLessonBlock(block: LessonBlock, topic: string) {
  // block.type 同时决定 payload 类型和 Renderer，TypeScript 会在各分支内完成类型收窄。
  return renderers[block.type]({ block, topic });
}
