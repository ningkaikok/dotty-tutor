import { parse } from "./client";
import { currentLearnerId } from "./identity";

export type VoiceTranscriptDraft = {
  revision: number;
  text: string;
  source: "manual" | "browser_speech_recognition" | "speech_to_text";
  confidence: Record<string, unknown> | null;
  ambiguityFlags: string[];
  needsConfirmation: boolean;
  updatedAt: number;
};

export type VoiceTurn = {
  turnId: string;
  transcriptRevision: number;
  transcript: string;
  response: string;
  createdAt: number;
};

export type VoiceSession = {
  sessionId: string;
  learnerId: string;
  chapterId: string;
  publicationId: string;
  lessonId: string;
  blockId: string;
  contextLabel: string;
  generation: number;
  active: boolean;
  draftTranscript: VoiceTranscriptDraft | null;
  confirmedTranscript: { revision: number; text: string; confirmedAt: number } | null;
  turns: VoiceTurn[];
  transcription: { available: boolean; provider: string | null; detail: string | null };
  recoveryToken?: string;
  expiresAt: number;
};

export type VoiceResponse = {
  status: "completed" | "superseded" | "duplicate";
  sessionId: string;
  generation: number;
  turn: VoiceTurn | null;
};

export type VoiceSessionScope = { learnerId: string; publicationId: string; blockId: string } & (
  | { sourceKind?: "chapter"; chapterId: string; lessonId: string }
  | { sourceKind: "courseware"; coursewareId: string; pageId: string }
);

export type VoiceSessionCredential = { sessionId: string; recoveryToken: string };

function sessionHeaders(credential: VoiceSessionCredential) {
  return { "X-Voice-Session-Token": credential.recoveryToken, "X-Learner-ID": currentLearnerId() };
}

export async function createVoiceSession(scope: VoiceSessionScope): Promise<VoiceSession> {
  return parse<VoiceSession>(await fetch(scope.sourceKind === "courseware" ? `/api/courseware/${encodeURIComponent(scope.coursewareId)}/voice-sessions` : "/api/voice/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Learner-ID": currentLearnerId() },
    body: JSON.stringify(scope.sourceKind === "courseware"
      ? { learnerId: scope.learnerId, publicationId: scope.publicationId, pageId: scope.pageId, blockId: scope.blockId }
      : { learnerId: scope.learnerId, publicationId: scope.publicationId, chapterId: scope.chapterId, lessonId: scope.lessonId, blockId: scope.blockId }),
  }));
}

export async function getVoiceSession(credential: VoiceSessionCredential): Promise<VoiceSession> {
  return parse<VoiceSession>(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}`, {
    headers: sessionHeaders(credential),
    cache: "no-store",
  }));
}

export async function saveVoiceTranscript(
  credential: VoiceSessionCredential,
  text: string,
  source: VoiceTranscriptDraft["source"] = "manual",
  browserConfidence: number | null = null,
): Promise<VoiceSession> {
  return parse<VoiceSession>(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}/transcript`, {
    method: "PUT",
    headers: { ...sessionHeaders(credential), "Content-Type": "application/json" },
    body: JSON.stringify({ text, source, browserConfidence }),
  }));
}

export async function transcribeVoiceAudio(
  credential: VoiceSessionCredential,
  audio: Blob,
  filename: string,
): Promise<{ session: VoiceSession; transcript: { text: string; confidence: Record<string, unknown> | null; ambiguityFlags: string[]; needsConfirmation: true } }> {
  const form = new FormData();
  form.set("audio", audio, filename);
  return parse(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}/transcribe`, {
    method: "POST",
    headers: sessionHeaders(credential),
    body: form,
  }));
}

export async function confirmVoiceTranscript(
  credential: VoiceSessionCredential,
  revision: number,
  text: string,
): Promise<VoiceSession> {
  return parse<VoiceSession>(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}/confirm-transcript`, {
    method: "POST",
    headers: { ...sessionHeaders(credential), "Content-Type": "application/json" },
    body: JSON.stringify({ revision, text }),
  }));
}

export async function requestVoiceResponse(
  credential: VoiceSessionCredential,
  input: { turnId: string; transcriptRevision: number; expectedGeneration: number },
  signal?: AbortSignal,
): Promise<VoiceResponse> {
  return parse<VoiceResponse>(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}/responses`, {
    method: "POST",
    headers: { ...sessionHeaders(credential), "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, turnId: input.turnId }),
    signal,
  }));
}

export async function cancelVoiceResponse(
  credential: VoiceSessionCredential,
  expectedGeneration: number,
): Promise<{ cancelled: boolean; session: VoiceSession }> {
  return parse(await fetch(`/api/voice/sessions/${encodeURIComponent(credential.sessionId)}/cancel`, {
    method: "POST",
    headers: { ...sessionHeaders(credential), "Content-Type": "application/json" },
    body: JSON.stringify({ expectedGeneration }),
  }));
}
