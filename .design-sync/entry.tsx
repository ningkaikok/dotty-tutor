// Pilot entry for /design-sync: the app has no library build, so this file
// selects the shared presentational components and the stylesheets they rely
// on. Keep it in sync with componentSrcMap in config.json.
//
// Mirrors apps/web/src/App.tsx: the Nunito font is imported before styles.css.
import "@fontsource-variable/nunito";
// Bright tokens (:root --c-*), global question/option styles and .evaluation-evidence.
import "../apps/web/src/styles.css";
// Bright overrides for the answering UI, scoped under .student-paper-shell.
import "../apps/web/src/apps/student/student.css";

export { default as MathText } from "../apps/web/src/MathText";
export { RichText } from "../apps/web/src/RichText";
export { QuestionContent } from "../apps/web/src/QuestionContent";
export { EvaluationEvidence } from "../apps/web/src/components/EvaluationEvidence";
