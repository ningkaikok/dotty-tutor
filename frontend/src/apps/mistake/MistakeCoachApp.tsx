import { useEffect, useState } from "react";
import { useMatch, useNavigate } from "react-router";
import { archiveMistake, loadMistake, loadMistakes } from "../../api";
import type { MistakeItem } from "../../types";
import { MistakeCapture } from "./components/MistakeCapture";
import { MistakeConfirm } from "./components/MistakeConfirm";
import { MistakeLibrary } from "./components/MistakeLibrary";
import { MistakeProgress } from "./components/MistakeProgress";
import { MistakeTutor } from "./components/MistakeTutor";
import "./mistake.css";

type MistakeScreen =
  | { name: "library" }
  | { name: "capture" }
  | { name: "progress" }
  | { name: "confirm"; mistakeId: string }
  | { name: "tutor"; mistakeId: string };

export function MistakeCoachApp() {
  const navigate = useNavigate();
  const captureMatch = useMatch("/mistakes/capture");
  const progressMatch = useMatch("/mistakes/progress");
  const confirmMatch = useMatch("/mistakes/:mistakeId/confirm");
  const tutorMatch = useMatch("/mistakes/:mistakeId/tutor");
  const screen: MistakeScreen = tutorMatch?.params.mistakeId
    ? { name: "tutor", mistakeId: tutorMatch.params.mistakeId }
    : confirmMatch?.params.mistakeId
    ? { name: "confirm", mistakeId: confirmMatch.params.mistakeId }
    : progressMatch
      ? { name: "progress" }
    : captureMatch
      ? { name: "capture" }
      : { name: "library" };
  const [items, setItems] = useState<MistakeItem[]>([]);
  const [selected, setSelected] = useState<MistakeItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const open = (path: string) => {
    navigate(path);
    window.scrollTo({ top: 0 });
  };

  const refreshLibrary = async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await loadMistakes());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "错题本加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (screen.name === "library") {
      void refreshLibrary();
      return;
    }
    if ((screen.name === "confirm" || screen.name === "tutor") && selected?.mistakeId !== screen.mistakeId) {
      setLoading(true);
      setError("");
      void loadMistake(screen.mistakeId)
        .then(setSelected)
        .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "错题加载失败"))
        .finally(() => setLoading(false));
    }
  }, [screen.name, screen.name === "confirm" || screen.name === "tutor" ? screen.mistakeId : ""]);

  const returnToLibrary = () => open("/mistakes");

  return (
    <main className="mistake-shell">
      <header className="mistake-header">
        <button className="route-back-button" onClick={screen.name === "library" ? () => navigate("/learn") : returnToLibrary}>
          {screen.name === "library" ? "← 学生学习空间" : "← 我的错题本"}
        </button>
        <div className="mistake-brand"><span>D</span><strong>Dotty 错题陪练</strong></div>
        <span className="phase-badge">{screen.name === "tutor" || screen.name === "progress" ? "PHASE 04" : "PHASE 02"}</span>
      </header>

      {screen.name === "library" && (
        <MistakeLibrary
          items={items}
          loading={loading}
          error={error}
          onCapture={() => open("/mistakes/capture")}
          onProgress={() => open("/mistakes/progress")}
          onOpen={(item) => {
            setSelected(item);
            open(`/mistakes/${item.mistakeId}/confirm`);
          }}
          onTutor={(item) => {
            setSelected(item);
            open(`/mistakes/${item.mistakeId}/tutor`);
          }}
          onArchive={(item) => {
            void archiveMistake(item.mistakeId)
              .then(() => setItems((current) => current.filter((candidate) => candidate.mistakeId !== item.mistakeId)))
              .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "归档失败"));
          }}
        />
      )}

      {screen.name === "progress" && <MistakeProgress />}

      {screen.name === "capture" && (
        <MistakeCapture
          onCreated={(item) => {
            setSelected(item);
            open(`/mistakes/${item.mistakeId}/confirm`);
          }}
        />
      )}

      {screen.name === "confirm" && loading && <div className="mistake-empty">正在读取识别结果…</div>}
      {screen.name === "confirm" && error && <p className="mistake-error" role="alert">{error}</p>}
      {screen.name === "confirm" && !loading && selected?.mistakeId === screen.mistakeId && (
        <MistakeConfirm
          key={selected.mistakeId}
          item={selected}
          onSaved={(saved) => {
            setSelected(saved);
            setItems((current) => [saved, ...current.filter((item) => item.mistakeId !== saved.mistakeId)]);
            returnToLibrary();
          }}
        />
      )}
      {screen.name === "tutor" && loading && <div className="mistake-empty">正在读取错题…</div>}
      {screen.name === "tutor" && error && <p className="mistake-error" role="alert">{error}</p>}
      {screen.name === "tutor" && !loading && selected?.mistakeId === screen.mistakeId && (
        <MistakeTutor key={selected.mistakeId} item={selected} />
      )}
    </main>
  );
}
