let activeAudio: HTMLAudioElement | null = null;
let speechRequestId = 0;

export function stopSpeech() {
  speechRequestId += 1;
  window.speechSynthesis?.cancel();
  activeAudio?.pause();
  activeAudio = null;
}

export async function speak(text: string) {
  const requestId = ++speechRequestId;
  window.speechSynthesis?.cancel();
  activeAudio?.pause();
  activeAudio = null;
  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error("TTS unavailable");
    if (requestId !== speechRequestId) return;
    const audio = new Audio(URL.createObjectURL(await response.blob()));
    activeAudio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(audio.src);
      if (activeAudio === audio) activeAudio = null;
    };
    await audio.play();
  } catch {
    if (requestId !== speechRequestId || !("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
  }
}
