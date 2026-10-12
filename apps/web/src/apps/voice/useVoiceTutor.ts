import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { playSpeech, stopSpeech } from "../../speech";
import {
  ApiRequestError,
} from "../../api/client";
import {
  cancelVoiceResponse,
  confirmVoiceTranscript,
  createVoiceSession,
  getVoiceSession,
  requestVoiceResponse,
  saveVoiceTranscript,
  transcribeVoiceAudio,
  type VoiceSession,
  type VoiceSessionCredential,
  type VoiceSessionScope,
} from "../../api/voice";

type RecognitionAlternativeLike = { transcript: string; confidence: number };
type RecognitionResultLike = { isFinal: boolean; 0: RecognitionAlternativeLike };
type RecognitionEventLike = Event & { resultIndex: number; results: ArrayLike<RecognitionResultLike> };
type RecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onerror: ((event: Event & { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(audioTrack?: MediaStreamTrack): void;
  stop(): void;
  abort(): void;
};
type RecognitionConstructor = new () => RecognitionLike;
type RecognitionWindow = Window & {
  SpeechRecognition?: RecognitionConstructor;
  webkitSpeechRecognition?: RecognitionConstructor;
};

export type VoiceTutorPanelProps = VoiceSessionScope & { blockTitle: string };

const LOCAL_STORAGE_PREFIX = "dotty.voice-session.v1:";

function recognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const browser = window as RecognitionWindow;
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition ?? null;
}

function recorderSupported(): boolean {
  return typeof navigator !== "undefined"
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof MediaRecorder !== "undefined";
}

function localStorageKey(scope: VoiceSessionScope): string {
  return `${LOCAL_STORAGE_PREFIX}${[scope.learnerId, scope.sourceKind === "courseware" ? `courseware:${scope.coursewareId}` : scope.chapterId, scope.publicationId, scope.sourceKind === "courseware" ? scope.pageId : scope.lessonId, scope.blockId].map(encodeURIComponent).join(":")}`;
}

function fileExtension(blob: Blob): string {
  const type = blob.type.split(";", 1)[0].toLowerCase();
  if (type === "audio/mp4" || type === "video/mp4") return ".m4a";
  if (type === "audio/wav" || type === "audio/x-wav") return ".wav";
  if (type === "audio/mpeg") return ".mp3";
  if (type === "audio/ogg") return ".ogg";
  if (type === "audio/flac") return ".flac";
  return ".webm";
}

export function useVoiceTutor(props: VoiceTutorPanelProps) {
  const { learnerId, publicationId, blockId } = props;
  const sourceKind = props.sourceKind ?? "chapter";
  const sourceId = props.sourceKind === "courseware" ? props.coursewareId : props.chapterId;
  const stepId = props.sourceKind === "courseware" ? props.pageId : props.lessonId;
  const scope = useMemo<VoiceSessionScope>(() => sourceKind === "courseware"
    ? { learnerId, publicationId, blockId, sourceKind: "courseware", coursewareId: sourceId, pageId: stepId }
    : { learnerId, publicationId, blockId, chapterId: sourceId, lessonId: stepId }, [learnerId, publicationId, blockId, sourceKind, sourceId, stepId]);
  const scopeKey = useMemo(() => localStorageKey(scope), [scope]);
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [credential, setCredential] = useState<VoiceSessionCredential | null>(null);
  const [transcript, setTranscript] = useState("");
  const [transcriptSource, setTranscriptSource] = useState<"manual" | "browser_speech_recognition" | "speech_to_text">("manual");
  const [browserConfidence, setBrowserConfidence] = useState<number | null>(null);
  const [recordingBlob, setRecordingBlob] = useState<Blob | null>(null);
  const [recordingUrl, setRecordingUrl] = useState("");
  const [recording, setRecording] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [pendingGeneration, setPendingGeneration] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mountedRef = useRef(false);
  const credentialRef = useRef<VoiceSessionCredential | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const recordingUrlRef = useRef("");
  const recordingTimerRef = useRef<number | null>(null);
  const recognitionRef = useRef<RecognitionLike | null>(null);
  const responseControllerRef = useRef<AbortController | null>(null);
  const pendingGenerationRef = useRef<number | null>(null);
  const draftSequenceRef = useRef(0);
  const draftQueueRef = useRef<Promise<void>>(Promise.resolve());

  const setCurrentSession = useCallback((next: VoiceSession) => {
    setSession(next);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let disposed = false;
    const load = async () => {
      setError("");
      try {
        const saved = window.localStorage.getItem(scopeKey);
        if (saved) {
          const candidate = JSON.parse(saved) as VoiceSessionCredential;
          if (candidate.sessionId && candidate.recoveryToken) {
            try {
              const restored = await getVoiceSession(candidate);
              if (disposed) return;
              credentialRef.current = candidate;
              setCredential(candidate);
              setCurrentSession(restored);
              if (restored.active) {
                pendingGenerationRef.current = restored.generation;
                setPendingGeneration(restored.generation);
                setBusy(true);
              }
              setTranscript(restored.draftTranscript?.text ?? "");
              setTranscriptSource(restored.draftTranscript?.source === "browser_speech_recognition" || restored.draftTranscript?.source === "speech_to_text" ? restored.draftTranscript.source : "manual");
              const score = restored.draftTranscript?.confidence?.score;
              setBrowserConfidence(typeof score === "number" ? score : null);
              return;
            } catch (restoreError) {
              if (!(restoreError instanceof ApiRequestError) || restoreError.status !== 404) throw restoreError;
              window.localStorage.removeItem(scopeKey);
            }
          }
        }
        const created = await createVoiceSession(scope);
        if (disposed) return;
        if (!created.recoveryToken) throw new Error("语音会话没有返回恢复凭据");
        const nextCredential = { sessionId: created.sessionId, recoveryToken: created.recoveryToken };
        window.localStorage.setItem(scopeKey, JSON.stringify(nextCredential));
        credentialRef.current = nextCredential;
        setCredential(nextCredential);
        setCurrentSession(created);
      } catch (loadError) {
        if (!disposed) setError(loadError instanceof Error ? loadError.message : "语音会话加载失败");
      }
    };
    void load();

    return () => {
      disposed = true;
      mountedRef.current = false;
      responseControllerRef.current?.abort();
      responseControllerRef.current = null;
      const pendingGeneration = pendingGenerationRef.current;
      const activeCredential = credentialRef.current;
      if (pendingGeneration !== null && activeCredential) {
        void cancelVoiceResponse(activeCredential, pendingGeneration).catch(() => undefined);
      }
      pendingGenerationRef.current = null;
      recognitionRef.current?.abort();
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      if (recordingTimerRef.current !== null) window.clearTimeout(recordingTimerRef.current);
      stopSpeech();
      if (recordingUrlRef.current) URL.revokeObjectURL(recordingUrlRef.current);
    };
  }, [scope, scopeKey, setCurrentSession]);

  useEffect(() => {
    if (!session || !credential || !transcript.trim()) return;
    const draft = session.draftTranscript;
    if (draft?.text === transcript.trim()) return;
    const sequence = ++draftSequenceRef.current;
    const timer = window.setTimeout(() => {
      draftQueueRef.current = draftQueueRef.current.then(async () => {
        if (sequence !== draftSequenceRef.current) return;
        try {
          const next = await saveVoiceTranscript(credential, transcript, transcriptSource, browserConfidence);
          if (mountedRef.current && sequence === draftSequenceRef.current) setCurrentSession(next);
        } catch (saveError) {
          if (mountedRef.current && sequence === draftSequenceRef.current) setError(saveError instanceof Error ? saveError.message : "转写保存失败");
        }
      });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [browserConfidence, credential, session, setCurrentSession, transcript, transcriptSource]);

  const releaseRecordingUrl = () => {
    if (recordingUrlRef.current) URL.revokeObjectURL(recordingUrlRef.current);
    recordingUrlRef.current = "";
    setRecordingUrl("");
  };

  const startRecording = async () => {
    setError("");
    setNotice("");
    releaseRecordingUrl();
    setRecordingBlob(null);
    try {
      if (!recorderSupported()) throw new Error("此浏览器不支持麦克风录音，请上传录音或手动输入转写文字");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      mediaStreamRef.current = stream;
      const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks: BlobPart[] = [];
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => {
        if (mountedRef.current) setError("录音中断，请重新录制或上传录音");
      };
      recorder.onstop = () => {
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = null;
        if (!mountedRef.current) return;
        const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
        if (!blob.size) {
          setError("没有录到音频，请重试或手动输入转写文字");
          return;
        }
        const url = URL.createObjectURL(blob);
        recordingUrlRef.current = url;
        setRecordingBlob(blob);
        setRecordingUrl(url);
        setNotice("录音已完成。先回放并检查声音，再使用浏览器/服务端转写或手动输入文字。");
        recorderRef.current = null;
      };
      recorder.start();
      setRecording(true);
      recordingTimerRef.current = window.setTimeout(() => {
        if (recorder.state === "recording") recorder.stop();
        setRecording(false);
      }, 60_000);
    } catch (recordError) {
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      setRecording(false);
      setError(recordError instanceof Error ? recordError.message : "无法开始录音");
    }
  };

  const stopRecording = () => {
    if (recordingTimerRef.current !== null) window.clearTimeout(recordingTimerRef.current);
    recordingTimerRef.current = null;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    setRecording(false);
  };

  const transcribeInBrowser = async () => {
    const Constructor = recognitionConstructor();
    const player = audioRef.current as (HTMLAudioElement & { captureStream?: () => MediaStream; mozCaptureStream?: () => MediaStream }) | null;
    if (!Constructor || !player || !recordingUrl) {
      setError("此浏览器不能转写这段录音；可上传到已配置的服务或手动输入文字");
      return;
    }
    const capture = player.captureStream ?? player.mozCaptureStream;
    if (!capture) {
      setError("浏览器不能把录音交给语音识别；可上传到已配置的服务或手动输入文字");
      return;
    }
    const stream = capture.call(player);
    const track = stream.getAudioTracks()[0];
    if (!track) {
      stream.getTracks().forEach((item) => item.stop());
      setError("录音没有可识别的音轨，请手动输入文字");
      return;
    }
    const recognition = new Constructor();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognitionRef.current = recognition;
    setRecognizing(true);
    setError("");
    setNotice("浏览器正在分析这段录音；识别结果仍须由你检查并确认。");
    const audio = player;
    audio.currentTime = 0;
    recognition.onresult = (event) => {
      const phrases: string[] = [];
      const scores: number[] = [];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (!result.isFinal) continue;
        const alternative = result[0];
        if (alternative.transcript.trim()) phrases.push(alternative.transcript.trim());
        if (Number.isFinite(alternative.confidence) && alternative.confidence >= 0 && alternative.confidence <= 1) scores.push(alternative.confidence);
      }
      if (phrases.length) {
        setTranscript(phrases.join(" "));
        setTranscriptSource("browser_speech_recognition");
        setBrowserConfidence(scores.length ? scores.reduce((sum, item) => sum + item, 0) / scores.length : null);
        setNotice("浏览器识别已结束。请先编辑或确认显示的文字；识别置信值仅供参考。");
      }
    };
    recognition.onerror = (event) => {
      setError(event.error ? `浏览器语音识别失败：${event.error}。你仍可手动输入转写文字。` : "浏览器语音识别失败，你仍可手动输入转写文字");
    };
    recognition.onend = () => {
      stream.getTracks().forEach((item) => item.stop());
      if (mountedRef.current) setRecognizing(false);
      if (recognitionRef.current === recognition) recognitionRef.current = null;
    };
    try {
      await audio.play();
      recognition.start(track);
    } catch (recognitionError) {
      recognition.abort();
      stream.getTracks().forEach((item) => item.stop());
      setRecognizing(false);
      setError(recognitionError instanceof Error ? `此浏览器不能识别录音：${recognitionError.message}` : "此浏览器不能识别录音");
    }
  };

  const transcribeWithServer = async (blob = recordingBlob, filename = `recording${fileExtension(recordingBlob ?? new Blob())}`) => {
    if (!session || !credential || !blob) return;
    setBusy(true);
    setError("");
    setNotice("录音正在送到已配置的服务端转写模块；原始录音不会存入语音会话。");
    try {
      const result = await transcribeVoiceAudio(credential, blob, filename);
      setCurrentSession(result.session);
      setTranscript(result.transcript.text);
      setTranscriptSource("speech_to_text");
      setBrowserConfidence(null);
      setNotice("服务端转写完成。请编辑并确认文字，再让导师回应。");
    } catch (transcriptionError) {
      setError(transcriptionError instanceof Error ? transcriptionError.message : "服务端转写失败");
      setNotice("");
    } finally {
      setBusy(false);
    }
  };

  const selectAudioFile = (file: File | undefined) => {
    if (!file) return;
    if (file.size <= 0 || file.size > 16 * 1024 * 1024) {
      setError("录音为空或超过 16 MB，请选择较短的录音或手动输入文字");
      return;
    }
    releaseRecordingUrl();
    const url = URL.createObjectURL(file);
    recordingUrlRef.current = url;
    setRecordingUrl(url);
    setRecordingBlob(file);
    setNotice("录音文件已载入。可回放后上传转写；没有可用转写服务时请手动输入文字。");
    setError("");
  };

  const confirmAndRespond = async () => {
    if (!session || !credential || !transcript.trim()) return;
    setBusy(true);
    // Invalidate a pending debounce save; this operation writes and confirms
    // the latest text atomically from the learner's point of view.
    draftSequenceRef.current += 1;
    setError("");
    setNotice("先保存并确认这段转写，然后开始生成当前步骤的追问。");
    try {
      await draftQueueRef.current;
      const saved = await saveVoiceTranscript(credential, transcript, transcriptSource, browserConfidence);
      setCurrentSession(saved);
      const revision = saved.draftTranscript?.revision;
      if (!revision) throw new Error("转写还没有保存，无法确认");
      const confirmed = await confirmVoiceTranscript(credential, revision, transcript);
      setCurrentSession(confirmed);
      const turnId = crypto.randomUUID();
      const expectedGeneration = confirmed.generation;
      const claimedGeneration = expectedGeneration + 1;
      pendingGenerationRef.current = claimedGeneration;
      setPendingGeneration(claimedGeneration);
      const controller = new AbortController();
      responseControllerRef.current = controller;
      const result = await requestVoiceResponse(credential, {
        turnId,
        transcriptRevision: revision,
        expectedGeneration,
      }, controller.signal);
      pendingGenerationRef.current = null;
      setPendingGeneration(null);
      responseControllerRef.current = null;
      if (!mountedRef.current) return;
      const restored = await getVoiceSession(credential);
      setCurrentSession(restored);
      if (result.status === "superseded") {
        setNotice("这条追问已取消，没有覆盖当前步骤。可以重新确认转写再试一次。");
        return;
      }
      if (result.turn?.response) {
        setNotice("导师已回应。转写只用于本轮对话，不会记作答题或掌握证据。");
        setSpeaking(true);
        void playSpeech(result.turn.response).finally(() => {
          if (mountedRef.current) setSpeaking(false);
        });
      }
    } catch (responseError) {
      pendingGenerationRef.current = null;
      setPendingGeneration(null);
      responseControllerRef.current = null;
      if (responseError instanceof DOMException && responseError.name === "AbortError") return;
      setError(responseError instanceof Error ? responseError.message : "导师暂时无法回应，请稍后重试");
      setNotice("");
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const cancelResponse = async () => {
    const activeCredential = credentialRef.current;
    const generation = pendingGenerationRef.current;
    if (!activeCredential || generation === null) return;
    responseControllerRef.current?.abort();
    responseControllerRef.current = null;
    pendingGenerationRef.current = null;
    setPendingGeneration(null);
    stopSpeech();
    setSpeaking(false);
    try {
      const result = await cancelVoiceResponse(activeCredential, generation);
      if (mountedRef.current) {
        setCurrentSession(result.session);
        setNotice(result.cancelled ? "已取消这一步的回复生成。" : "当前回复已结束或被新转写替代，已同步本步骤会话。");
      }
    } catch (cancelError) {
      if (mountedRef.current) setError(cancelError instanceof Error ? cancelError.message : "取消请求未完成");
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const stopTutorSpeech = () => {
    stopSpeech();
    setSpeaking(false);
  };

  const savedDraft = session?.draftTranscript;
  const savedConfidence = savedDraft?.confidence;
  const hasBrowserRecognition = Boolean(recognitionConstructor());

  return {
    session, recordingUrl, recordingBlob, recording, hasBrowserRecognition, audioRef,
    recordingSupported: recorderSupported(),
    startRecording, stopRecording, busy, speaking, stopTutorSpeech, recognizing, transcribeInBrowser,
    transcribeWithServer, selectAudioFile, transcript, setTranscript,
    setTranscriptSource, setBrowserConfidence, browserConfidence, savedDraft, savedConfidence,
    error, setError, notice, confirmAndRespond, cancelResponse, pendingGeneration,
  };
}
