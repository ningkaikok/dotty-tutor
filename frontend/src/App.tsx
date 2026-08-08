import { useEffect, useState } from "react";
import { ProductHome } from "./apps/home/ProductHome";
import { MistakeCoachApp } from "./apps/mistake/MistakeCoachApp";
import { TextbookApp } from "./apps/textbook/TextbookApp";
import "./styles.css";

type AppRoute = "/" | "/textbooks" | "/mistakes";

function getRoute(pathname = window.location.pathname): AppRoute {
  if (pathname === "/textbooks" || pathname.startsWith("/textbooks/")) return "/textbooks";
  if (pathname === "/mistakes" || pathname.startsWith("/mistakes/")) return "/mistakes";
  return "/";
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => getRoute());

  useEffect(() => {
    const onPopState = () => setRoute(getRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    document.title = route === "/textbooks"
      ? "教材互动学习 · Dotty Tutor"
      : route === "/mistakes"
        ? "AI 错题陪练 · Dotty Tutor"
        : "Dotty Tutor · 个人 AI 学习工具";
  }, [route]);

  const navigate = (nextRoute: AppRoute) => {
    if (window.location.pathname !== nextRoute) window.history.pushState({}, "", nextRoute);
    setRoute(nextRoute);
    window.scrollTo({ top: 0 });
  };

  if (route === "/textbooks") return <TextbookApp onExit={() => navigate("/")} />;
  if (route === "/mistakes") return <MistakeCoachApp onExit={() => navigate("/")} />;
  return <ProductHome onNavigate={navigate} />;
}
