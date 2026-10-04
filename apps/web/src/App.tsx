import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";
import "./styles.css";
import { PRODUCT_TERMS } from "./productTerms";
import { ProtectedAccess } from "./auth/ProtectedAccess";

// 每个角色入口拥有独立的路由级代码包。学生打开轻量学习空间时，不应同时下载体积更大的内容生产工作台。
// 当前页面组件采用具名导出，而 React.lazy 只接受 default，因此这里显式完成一次导出映射。
const PromptManagerApp = lazy(() => import("./apps/prompts/PromptManagerApp").then((module) => ({ default: module.PromptManagerApp })));
const ProductHome = lazy(() => import("./apps/home/ProductHome").then((module) => ({ default: module.ProductHome })));
const StudentLearningApp = lazy(() => import("./apps/student/StudentLearningApp").then((module) => ({ default: module.StudentLearningApp })));
const PublishedPaperApp = lazy(() => import("./apps/student/PublishedPaperApp").then((module) => ({ default: module.PublishedPaperApp })));
const MistakeCoachApp = lazy(() => import("./apps/mistake/MistakeCoachApp").then((module) => ({ default: module.MistakeCoachApp })));
const MaterialsApp = lazy(() => import("./apps/materials/MaterialsApp").then((module) => ({ default: module.MaterialsApp })));
const TextbookApp = lazy(() => import("./apps/textbook/TextbookApp").then((module) => ({ default: module.TextbookApp })));
const ModelMetricsApp = lazy(() => import("./apps/metrics/ModelMetricsApp").then((module) => ({ default: module.ModelMetricsApp })));
const DependencyPreflightApp = lazy(() => import("./apps/metrics/DependencyPreflightApp").then((module) => ({ default: module.DependencyPreflightApp })));
const TeacherClassroomApp = lazy(() => import("./apps/teacher/TeacherClassroomApp").then((module) => ({ default: module.TeacherClassroomApp })));
const ChapterStudioApp = lazy(() => import("./apps/chapters/ChapterStudioApp").then((module) => ({ default: module.ChapterStudioApp })));
const PublishedChapterApp = lazy(() => import("./apps/student/PublishedChapterApp").then((module) => ({ default: module.PublishedChapterApp })));

function PageTitle() {
  const { pathname } = useLocation();

  useEffect(() => {
    document.title = pathname.startsWith("/studio")
      ? "教材与课程 · Dotty Tutor"
      : pathname.startsWith("/learn")
        ? "学生学习空间 · Dotty Tutor"
      : pathname.startsWith("/mistakes")
          ? `AI 错题${PRODUCT_TERMS.tutoring} · Dotty Tutor`
          : pathname.startsWith("/teacher")
            ? "教师工作台 · Dotty Tutor"
          : "Dotty Tutor · 个人 AI 学习工具";
  }, [pathname]);

  return null;
}

function AppRoutes() {
  // 路由表与 BrowserRouter 分开，保证 useLocation 等 Hook 一定运行在 Router 上下文中，
  // 同时让路由表在测试中可以被 MemoryRouter 单独装配。
  return (
    <>
      <PageTitle />
      <Suspense fallback={<main className="center-state"><span>正在打开学习空间…</span></main>}>
        <Routes>
          <Route index element={<ProductHome />} />
          <Route path="learn/chapters/:chapterId" element={<PublishedChapterApp />} />
          <Route path="learn/papers/:publicationId" element={<PublishedPaperApp />} />
          <Route path="learn/*" element={<StudentLearningApp />} />
          <Route path="studio" element={<MaterialsApp />} />
          <Route path="studio/import" element={<TextbookApp />} />
          <Route path="studio/chapters" element={<Navigate to="/studio" replace />} />
          <Route path="studio/chapters/new" element={<ChapterStudioApp />} />
          <Route path="studio/chapters/:chapterId" element={<ChapterStudioApp />} />
          <Route path="studio/prompts" element={<PromptManagerApp />} />
          <Route path="studio/metrics" element={<ModelMetricsApp />} />
          <Route path="studio/dependency-preflight" element={<DependencyPreflightApp />} />
          <Route path="teacher/*" element={<TeacherClassroomApp />} />
          <Route path="studio/*" element={<Navigate to="/studio" replace />} />
          <Route path="mistakes/*" element={<MistakeCoachApp />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ProtectedAccess><AppRoutes /></ProtectedAccess>
    </BrowserRouter>
  );
}
