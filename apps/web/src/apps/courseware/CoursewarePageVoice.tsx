import { useState } from "react";
import { useLearnerId } from "../../api/identity";
import { Button } from "../../components/ui";
import { VoiceTutorPanel } from "../voice/VoiceTutorPanel";
import type { CoursewarePageView } from "./coursewarePresentation";

/** Open tutoring only for a public teaching block; no activity/answer block can be selected. */
export function CoursewarePageVoice({ coursewareId, publicationId, page }: { coursewareId: string; publicationId: string; page: CoursewarePageView }) {
  const learnerId = useLearnerId();
  const [open, setOpen] = useState(false);
  const block = page.blocks.find((item) => ["text", "formula", "image", "word_cards"].includes(item.kind));
  if (!block || !("blockId" in block)) return null;
  return <div className="courseware-page-voice"><Button onClick={() => setOpen((value) => !value)} aria-expanded={open}>{open ? "收起本页语音追问" : "围绕本页讲解语音追问"}</Button>
    {open && <VoiceTutorPanel key={learnerId} learnerId={learnerId} sourceKind="courseware" coursewareId={coursewareId}
      publicationId={publicationId} pageId={page.pageId} blockId={block.blockId} blockTitle={page.title} />}
  </div>;
}
