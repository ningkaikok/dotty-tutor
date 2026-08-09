let activeAudio: HTMLAudioElement | null = null;
// 每次播放递增令牌，使所有旧的异步 continuation 失效。仅暂停 Audio 不够：
// 音频请求可能在用户已经切换步骤后才返回，如果不校验令牌就会播放错误步骤的旁白。
let speechRequestId = 0;
let activePlaybackResolve: (() => void) | null = null;
// 缓存 Promise 而不仅是已完成的 Blob，让相同文本的并发预热共享同一个 HTTP 请求。
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

/** 只预取音频而不播放，使下一教学步骤切换时尽可能直接命中缓存。 */
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
