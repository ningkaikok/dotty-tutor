import { GeometryCanvas } from "../GeometryCanvas";
import MathText from "../MathText";
import type { LessonBlock } from "../types";

interface LessonRendererProps {
  block: LessonBlock;
  topic: string;
}

type LessonBlockRenderer = (props: LessonRendererProps) => React.ReactNode;

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
    ? <aside className="lesson-annotation">{block.payload.text}</aside>
    : null,
  quiz: () => null,
  hint: ({ block }) => block.type === "hint"
    ? <aside className="lesson-hint">{block.payload.hint}</aside>
    : null,
};

export function renderLessonBlock(block: LessonBlock, topic: string) {
  return renderers[block.type]({ block, topic });
}
