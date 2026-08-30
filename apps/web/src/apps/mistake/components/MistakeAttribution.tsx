import type { MistakeErrorReason } from "../../../types/index";
import type { MistakeAttribution as MistakeAttributionData } from "../attribution";
import { errorReasonLabel } from "../errorReasons";

interface MistakeAttributionProps {
  attribution: MistakeAttributionData;
}

function AttributionLabel({ prefix, reason }: { prefix: string; reason: MistakeErrorReason }) {
  return <span className="tutor-attribution-label"><small>{prefix}</small>{errorReasonLabel(reason)}</span>;
}

/**
 * 并列展示学生自评与 Dotty 判断。
 *
 * 自评没有对错——它是元认知信号，两者不一致时的文案必须是学习契机而不是纠错；
 * 学生跳过自评时也只展示 AI 判断，不提示缺失。调用方必须保证本组件只在自评
 * 完成或跳过之后渲染，先看到 AI 判断会让自评这一步失去意义。
 */
export function MistakeAttribution({ attribution }: MistakeAttributionProps) {
  const { selfAssessment, aiAssessment } = attribution;

  if (!aiAssessment) {
    if (!selfAssessment) return null;
    return (
      <section className="tutor-attribution">
        <p>你的自评：{errorReasonLabel(selfAssessment)}。Dotty 还在判断中，等有更多证据再一起看看。</p>
      </section>
    );
  }

  if (!selfAssessment) {
    return (
      <section className="tutor-attribution">
        <p>Dotty 判断：{errorReasonLabel(aiAssessment)}</p>
      </section>
    );
  }

  if (selfAssessment === aiAssessment) {
    return (
      <section className="tutor-attribution tutor-attribution-confirmed">
        <p>你的判断和 Dotty 一致：{errorReasonLabel(selfAssessment)}</p>
      </section>
    );
  }

  return (
    <section className="tutor-attribution">
      <p>你觉得是「{errorReasonLabel(selfAssessment)}」，Dotty 判断是「{errorReasonLabel(aiAssessment)}」。看看下面的分析，再想想这两者的区别在哪。</p>
      <div className="tutor-attribution-labels">
        <AttributionLabel prefix="你的自评" reason={selfAssessment} />
        <AttributionLabel prefix="Dotty 判断" reason={aiAssessment} />
      </div>
    </section>
  );
}
