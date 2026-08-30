/**
 * LessonBlock 渲染器注册表：块类型 → 渲染组件的查表边界。
 *
 * 设计约束：播放器只负责步骤与播放状态，不依赖任何具体内容块的实现；
 * 新增内容类型走三步——补充类型契约、实现 Renderer、注册一条记录，
 * 不在 LessonPlayer 里增加条件分支。公式一律经 MathText/KaTeX 渲染，
 * 不允许回到 Canvas fillText 路径。
 */
import { GeometryCanvas } from "../GeometryCanvas";
import MathText from "../MathText";
import { RichText } from "../RichText";
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
    ? <RichText text={block.payload.markdown} className="lesson-markdown" />
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
    ? <aside className="lesson-annotation"><RichText text={block.payload.text} /></aside>
    : null,
  quiz: () => null,
  hint: ({ block }) => block.type === "hint"
    ? <aside className="lesson-hint"><RichText text={block.payload.hint} /></aside>
    : null,
};

export function renderLessonBlock(block: LessonBlock, topic: string) {
  // block.type 同时决定 payload 类型和 Renderer，TypeScript 会在各分支内完成类型收窄。
  return renderers[block.type]({ block, topic });
}
