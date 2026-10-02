import type { ExerciseAttemptInput } from "../../types/index";

/**
 * Offline attempts are kept in one versioned envelope so a learner can use
 * several publications on the same device without one queue leaking into
 * another. The scope is deliberately part of every record rather than only
 * the storage key: it lets us migrate the old unscoped queue safely.
 */
export const OFFLINE_ATTEMPT_QUEUE_KEY = "dotty-learning-pending-attempts:v2";
export const LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY = "dotty-learning-pending-attempts";
/** Legacy records without a verifiable session are retained here for manual recovery, never replayed. */
export const QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY = "dotty-learning-pending-attempts:quarantine";

export interface OfflineAttemptScope {
  learnerId: string;
  publicationId: string;
  sessionId: string;
}

export interface PendingAttempt {
  learnerId: string;
  publicationId: string;
  sessionId: string;
  attempt: ExerciseAttemptInput;
}

interface LegacyPendingAttempt {
  sessionId?: string;
  attempt?: ExerciseAttemptInput;
}

interface QuarantinedLegacyPendingAttempt {
  record: LegacyPendingAttempt;
  reason: "legacy_session_unbound" | "legacy_session_out_of_scope" | "legacy_record_invalid";
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Raised when the browser refuses localStorage access (private mode, quota, or policy). */
export class OfflineAttemptQueueStorageError extends Error {
  readonly code = "offline_attempt_storage_unavailable";

  constructor(message = "无法使用本机离线存储，答案不会被暂存，请保持网络连接后重试") {
    super(message);
    this.name = "OfflineAttemptQueueStorageError";
  }
}

function browserStorage(): StorageLike {
  try {
    if (typeof localStorage === "undefined") throw new Error("localStorage is not available");
    // Accessing localStorage itself can throw in privacy-restricted browsers.
    const storage = localStorage;
    void storage.length;
    return storage;
  } catch {
    throw new OfflineAttemptQueueStorageError();
  }
}

function storageOrDefault(storage?: StorageLike): StorageLike {
  return storage ?? browserStorage();
}

function readArray<T>(storage: StorageLike, key: string): T[] {
  let raw: string | null;
  try {
    raw = storage.getItem(key);
  } catch {
    throw new OfflineAttemptQueueStorageError();
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed as T[] : [];
  } catch {
    // A damaged queue must not stop the student page. The next write replaces
    // it with a valid envelope, while valid records in the other queue remain.
    return [];
  }
}

function writeArray<T>(storage: StorageLike, key: string, value: T[]): void {
  try {
    if (value.length) storage.setItem(key, JSON.stringify(value));
    else storage.removeItem(key);
  } catch {
    throw new OfflineAttemptQueueStorageError();
  }
}

function sameAttempt(left: ExerciseAttemptInput, right: ExerciseAttemptInput): boolean {
  return left.attemptId === right.attemptId;
}

function sameScope(left: PendingAttempt, right: OfflineAttemptScope): boolean {
  return left.learnerId === right.learnerId
    && left.publicationId === right.publicationId
    && left.sessionId === right.sessionId;
}

function migrateLegacy(
  storage: StorageLike,
  scope: OfflineAttemptScope,
  replacedSessionId: string,
  records: PendingAttempt[],
): PendingAttempt[] {
  const legacy = readArray<LegacyPendingAttempt>(storage, LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY);
  if (!legacy.length) return records;

  const quarantined: QuarantinedLegacyPendingAttempt[] = [];
  const migrated: PendingAttempt[] = [];
  for (const item of legacy) {
    const sessionId = typeof item.sessionId === "string" ? item.sessionId : "";
    const attempt = item.attempt;
    // The old queue had no learner/publication fields. Only a session returned
    // by the current scope (or its explicitly replaced session) can establish
    // ownership. In particular, an empty session must never be guessed to
    // belong to whichever learner happens to open the app next.
    const canMigrate = Boolean(attempt && sessionId)
      && (sessionId === scope.sessionId || (Boolean(replacedSessionId) && sessionId === replacedSessionId));
    if (!canMigrate || !attempt) {
      quarantined.push({
        record: item,
        reason: !attempt
          ? "legacy_record_invalid"
          : sessionId
            ? "legacy_session_out_of_scope"
            : "legacy_session_unbound",
      });
      continue;
    }
    if (!migrated.some((candidate) => sameAttempt(candidate.attempt, attempt))) {
      migrated.push({ ...scope, attempt });
    }
  }
  if (!migrated.length && !quarantined.length) return records;

  const next = [...records];
  for (const item of migrated) {
    if (!next.some((candidate) => sameScope(candidate, scope) && sameAttempt(candidate.attempt, item.attempt))) {
      next.push(item);
    }
  }
  writeArray(storage, OFFLINE_ATTEMPT_QUEUE_KEY, next);
  writeArray(storage, LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY, []);
  if (quarantined.length) {
    const prior = readArray<QuarantinedLegacyPendingAttempt>(storage, QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY);
    writeArray(storage, QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY, [...prior, ...quarantined]);
  }
  return next;
}

/** Read only the pending attempts for one learner/publication/session scope. */
export function readPendingAttempts(
  scope: OfflineAttemptScope,
  options: { replacedSessionId?: string; storage?: StorageLike } = {},
): PendingAttempt[] {
  const storage = storageOrDefault(options.storage);
  let records = readArray<PendingAttempt>(storage, OFFLINE_ATTEMPT_QUEUE_KEY);
  records = migrateLegacy(storage, scope, options.replacedSessionId ?? "", records);

  // Attempts created while session creation was still in flight have a known
  // learner/publication but an empty session. Bind those records to the first
  // recovered session, including the old-session replacement case.
  let changed = false;
  records = records.map((item) => {
    const belongsToPublication = item.learnerId === scope.learnerId && item.publicationId === scope.publicationId;
    const needsBinding = belongsToPublication
      && (item.sessionId === "" || item.sessionId === options.replacedSessionId)
      && item.sessionId !== scope.sessionId;
    if (!needsBinding) return item;
    changed = true;
    return { ...item, sessionId: scope.sessionId };
  });
  if (changed) writeArray(storage, OFFLINE_ATTEMPT_QUEUE_KEY, records);

  return records.filter((item) => sameScope(item, scope));
}

/** Add or replace one attempt while preserving attempts from other scopes. */
export function enqueuePendingAttempt(
  scope: OfflineAttemptScope,
  attempt: ExerciseAttemptInput,
  options: { storage?: StorageLike } = {},
): void {
  const storage = storageOrDefault(options.storage);
  // This also performs legacy migration relevant to this scope before writing.
  readPendingAttempts(scope, { storage: options.storage });
  const records = readArray<PendingAttempt>(storage, OFFLINE_ATTEMPT_QUEUE_KEY);
  const next = records.filter((item) => !(sameScope(item, scope) && sameAttempt(item.attempt, attempt)));
  next.push({ ...scope, attempt });
  writeArray(storage, OFFLINE_ATTEMPT_QUEUE_KEY, next);
}

/** Remove successfully delivered attempts from exactly one scope. */
export function removePendingAttempts(
  scope: OfflineAttemptScope,
  attemptIds: Iterable<string>,
  options: { storage?: StorageLike } = {},
): void {
  const storage = storageOrDefault(options.storage);
  const ids = new Set(attemptIds);
  if (!ids.size) return;
  const records = readArray<PendingAttempt>(storage, OFFLINE_ATTEMPT_QUEUE_KEY);
  writeArray(storage, OFFLINE_ATTEMPT_QUEUE_KEY, records.filter((item) =>
    !(sameScope(item, scope) && ids.has(item.attempt.attemptId))));
}
