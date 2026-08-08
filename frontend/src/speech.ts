let activeAudio: HTMLAudioElement | null = null;
// Incrementing this token invalidates every older async playback continuation.
// Pausing an Audio element alone is not enough because its fetch Promise may
// resolve later and otherwise start stale narration on a newly selected step.
let speechRequestId = 0;
let activePlaybackResolve: (() => void) | null = null;
// Cache Promises rather than only completed Blobs so concurrent preloads for the
// same sentence share one HTTP request while Qwen3-TTS is still synthesizing.
const speechCache = new Map<string, Promise<Blob | null>>();

function speechKey(text: string) {
  return text.trim();
}

function requestSpeech(text: string): Promise<Blob | null> {
  const key = speechKey(text);
  const cached = speechCache.get(key);
  if (cached) return cached;

  const request = fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: key }),
  })
    .then(async (response) => {
      if (!response.ok) throw new Error("TTS unavailable");
      return response.blob();
  })
    .catch(() => {
      speechCache.delete(key);
      return null;
    });
  speechCache.set(key, request);
  return request;
}

/** Start fetching audio without playing it, so the next lesson step is ready. */
export async function preloadSpeech(text: string) {
  await requestSpeech(text);
}

export function stopSpeech() {
  speechRequestId += 1;
  window.speechSynthesis?.cancel();
  activePlaybackResolve?.();
  activePlaybackResolve = null;
  activeAudio?.pause();
  activeAudio = null;
}

export async function playSpeech(text: string, onStart?: () => void) {
  const requestId = ++speechRequestId;
  window.speechSynthesis?.cancel();
  activePlaybackResolve?.();
  activePlaybackResolve = null;
  activeAudio?.pause();
  activeAudio = null;

  const blob = await requestSpeech(text);
  if (requestId !== speechRequestId) return;

  try {
    if (!blob) throw new Error("TTS unavailable");
    const audio = new Audio(URL.createObjectURL(blob));
    activeAudio = audio;
    const ended = new Promise<void>((resolve) => {
      activePlaybackResolve = resolve;
      audio.onended = () => resolve();
    });
    await audio.play();
    if (requestId !== speechRequestId) return;
    onStart?.();
    await ended;
    URL.revokeObjectURL(audio.src);
    if (activeAudio === audio) activeAudio = null;
    if (activePlaybackResolve) activePlaybackResolve = null;
  } catch {
    if (requestId !== speechRequestId || !("speechSynthesis" in window)) return;
    await new Promise<void>((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "zh-CN";
      utterance.rate = 0.95;
      utterance.onstart = () => onStart?.();
      utterance.onend = () => resolve();
      activePlaybackResolve = resolve;
      window.speechSynthesis.speak(utterance);
    });
    activePlaybackResolve = null;
  }
}

export async function speak(text: string) {
  await playSpeech(text);
}
