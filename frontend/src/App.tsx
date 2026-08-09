import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";
import "./styles.css";

// Each role or product area owns a route-level bundle. A student opening the
// lightweight learning space should not download the larger content studio.
// The named-export mapping is required because React.lazy expects `default`.
const ProductHome = lazy(() => import("./apps/home/ProductHome").then((module) => ({ default: module.ProductHome })));
const MistakeCoachApp = lazy(() => import("./apps/mistake/MistakeCoachApp").then((module) => ({ default: module.MistakeCoachApp })));
const TextbookApp = lazy(() => import("./apps/textbook/TextbookApp").then((module) => ({ default: module.TextbookApp })));

function PageTitle() {
  const { pathname } = useLocation();

  useEffect(() => {
    document.title = pathname.startsWith("/studio") || pathname.startsWith("/textbooks")
      ? "内容生产工作台 · Dotty Tutor"
      : pathname.startsWith("/mistakes") || pathname.startsWith("/learn")
        ? "学生学习空间 · Dotty Tutor"
        : "Dotty Tutor · 个人 AI 学习工具";
  }, [pathname]);

  return null;
}

function AppRoutes() {
  // Keep routing separate from BrowserRouter so hooks such as useLocation are
  // always rendered inside router context and remain easy to test in isolation.
  return (
    <>
      <PageTitle />
      <Suspense fallback={<main className="center-state"><span>正在打开学习空间…</span></main>}>
        <Routes>
          <Route index element={<ProductHome />} />
          <Route path="studio/*" element={<TextbookApp />} />
          {/* Compatibility redirects for routes that older releases published.
              /learn was a menu whose only working destinations both led here. */}
          <Route path="learn/*" element={<Navigate to="/mistakes" replace />} />
          <Route path="textbooks/*" element={<Navigate to="/studio" replace />} />
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
      <AppRoutes />
    </BrowserRouter>
  );
}
