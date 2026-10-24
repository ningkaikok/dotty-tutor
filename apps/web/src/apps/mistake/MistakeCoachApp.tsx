import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Notice, Skeleton } from "../../components/ui";
import { useMatch, useNavigate, useSearchParams } from "react-router";
import { AppHeader } from "../../components/ui";
import { archiveMistake, loadMistake, loadMistakes } from "../../api/mistakes";
import { useLearnerId } from "../../api/identity";
import { StudentNav } from "../student/StudentNav";
import type { MistakeItem } from "../../types/index";
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

interface MistakePageState {
  learnerId: string;
  items: MistakeItem[];
  selected: MistakeItem | null;
  loading: boolean;
  error: string;
}

export function MistakeCoachApp() {
  const navigate = useNavigate();
  const learnerId = useLearnerId();
  const learnerIdAtRender = useRef(learnerId);
  useLayoutEffect(() => { learnerIdAtRender.current = learnerId; }, [learnerId]);
  const [searchParams] = useSearchParams();
  const continuousMode = searchParams.get("continuous") === "1";
  const continueAfterMastery = useCallback(() => {
    if (learnerIdAtRender.current === learnerId) navigate("/learn/continue");
  }, [learnerId, navigate]);
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
  const [pageState, setPageState] = useState<MistakePageState>({
    learnerId,
    items: [],
    selected: null,
    loading: true,
    error: "",
  });
  // A learner switch hides the previous student's row and detail on the first
  // render, before the new request's effect starts.
  const currentState = pageState.learnerId === learnerId
    ? pageState
    : { learnerId, items: [], selected: null, loading: true, error: "" };
  const { items, selected, loading, error } = currentState;

  const open = (path: string) => {
    navigate(path);
    window.scrollTo({ top: 0 });
  };

  const activeMistakeId =
    screen.name === "confirm" || screen.name === "tutor" ? screen.mistakeId : "";
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    const isCurrent = () => !cancelled
      && !controller.signal.aborted
      && learnerIdAtRender.current === learnerId;

    if (screen.name === "library") {
      setPageState({ learnerId, items: [], selected: null, loading: true, error: "" });
      void loadMistakes(learnerId, controller.signal)
        .then((nextItems) => {
          if (isCurrent()) setPageState({ learnerId, items: nextItems, selected: null, loading: false, error: "" });
        })
        .catch((requestError) => {
          if (isCurrent()) setPageState({
            learnerId, items: [], selected: null, loading: false,
            error: requestError instanceof Error ? requestError.message : "错题本加载失败",
          });
        });
    } else if (activeMistakeId) {
      setPageState((current) => ({
        learnerId,
        items: current.learnerId === learnerId ? current.items : [],
        selected: null,
        loading: true,
        error: "",
      }));
      void loadMistake(activeMistakeId, controller.signal)
        .then((item) => {
          if (!isCurrent()) return;
          if (item.learnerId !== learnerId) throw new Error("错题不存在");
          setPageState((current) => ({
            learnerId,
            items: current.learnerId === learnerId ? current.items : [],
            selected: item,
            loading: false,
            error: "",
          }));
        })
        .catch((requestError) => {
          if (isCurrent()) setPageState((current) => ({
            learnerId,
            items: current.learnerId === learnerId ? current.items : [],
            selected: null,
            loading: false,
            error: requestError instanceof Error ? requestError.message : "错题加载失败",
          }));
        });
    }
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [activeMistakeId, learnerId, screen.name]);

  const setSelectedForLearner = (item: MistakeItem | null, requestLearnerId = learnerId) => {
    if (learnerIdAtRender.current !== requestLearnerId) return;
    setPageState((current) => ({
      learnerId: requestLearnerId,
      items: current.learnerId === requestLearnerId ? current.items : [],
      selected: item,
      loading: current.learnerId === requestLearnerId ? current.loading : false,
      error: current.learnerId === requestLearnerId ? current.error : "",
    }));
  };

  const setErrorForLearner = (message: string, requestLearnerId = learnerId) => {
    if (learnerIdAtRender.current !== requestLearnerId) return;
    setPageState((current) => ({
      learnerId: requestLearnerId,
      items: current.learnerId === requestLearnerId ? current.items : [],
      selected: current.learnerId === requestLearnerId ? current.selected : null,
      loading: false,
      error: message,
    }));
  };

  const returnToLibrary = () => open("/mistakes");
  const returnsToStudentHome = screen.name === "library" || screen.name === "progress";

  return (
    <main className="mistake-shell">
      <AppHeader
        backTo={returnsToStudentHome ? undefined : "/mistakes"}
        backLabel="返回我的错题本"
        subtitle="错题辅导"
      />
      {returnsToStudentHome && <StudentNav />}

      {screen.name === "library" && (
        <MistakeLibrary
          items={items}
          loading={loading}
          error={error}
          onCapture={() => open("/mistakes/capture")}
          onOpen={(item) => {
            if (item.learnerId !== learnerId) return;
            setSelectedForLearner(item);
            open(`/mistakes/${item.mistakeId}/confirm`);
          }}
          onTutor={(item) => {
            if (item.learnerId !== learnerId) return;
            setSelectedForLearner(item);
            open(`/mistakes/${item.mistakeId}/tutor`);
          }}
          onArchive={(item) => {
            const requestLearnerId = learnerId;
            void archiveMistake(item.mistakeId)
              .then(() => {
                if (learnerIdAtRender.current !== requestLearnerId) return;
                setPageState((current) => current.learnerId === requestLearnerId ? {
                  ...current,
                  items: current.items.filter((candidate) => candidate.mistakeId !== item.mistakeId),
                } : current);
              })
              .catch((requestError) => setErrorForLearner(requestError instanceof Error ? requestError.message : "归档失败", requestLearnerId));
          }}
        />
      )}

      {screen.name === "progress" && <MistakeProgress />}

      {screen.name === "capture" && (
        <MistakeCapture
          onCreated={(item) => {
            if (item.learnerId !== learnerId || learnerIdAtRender.current !== learnerId) return;
            setSelectedForLearner(item);
            open(`/mistakes/${item.mistakeId}/confirm`);
          }}
        />
      )}

      {screen.name === "confirm" && loading && <Skeleton label="正在读取识别结果…" />}
      {screen.name === "confirm" && error && <Notice tone="danger">{error}</Notice>}
      {screen.name === "confirm" && !loading && selected?.mistakeId === screen.mistakeId && (
        <MistakeConfirm
          key={selected.mistakeId}
          item={selected}
          onSaved={(saved) => {
            if (saved.learnerId !== learnerId || learnerIdAtRender.current !== learnerId) return;
            setPageState((current) => current.learnerId === learnerId ? {
              ...current,
              selected: saved,
              items: [saved, ...current.items.filter((item) => item.mistakeId !== saved.mistakeId)],
            } : current);
            if (continuousMode) open(`/mistakes/${encodeURIComponent(saved.mistakeId)}/tutor?continuous=1`);
            else returnToLibrary();
          }}
        />
      )}
      {screen.name === "tutor" && loading && <Skeleton label="正在读取错题…" />}
      {screen.name === "tutor" && error && <Notice tone="danger">{error}</Notice>}
      {screen.name === "tutor" && !loading && selected?.mistakeId === screen.mistakeId && (
        <MistakeTutor
          key={`${learnerId}:${selected.mistakeId}`}
          item={selected}
          autoContinue={continuousMode}
          onMastered={continueAfterMastery}
        />
      )}
    </main>
  );
}
