import { useEffect, useRef, useState } from "react";
import {
  cancelMistakeImportJob,
  loadMistakeImportJob,
  queueMistakeImport,
  retryMistakeImportJob,
} from "../../../api/mistakes";
import type { MistakeItem } from "../../../types/index";
import type { BackgroundJob } from "../../../types/textbook";
import { useLearnerId } from "../../../api/identity";
import { cropImageFile, ImageCropper, type CropSelection } from "./ImageCropper";

interface MistakeCaptureProps {
  onCreated: (item: MistakeItem) => void;
}

const EMPTY_CROP: CropSelection = { top: 0, right: 0, bottom: 0, left: 0 };
const IMAGE_SUFFIXES = [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"];
const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif", "image/bmp", "image/tiff"]);
const MAX_MANUAL_RETRIES = 2;

export function mistakeImportJobStorageKey(learnerId: string): string {
  return `dotty:mistake-import-job:${encodeURIComponent(learnerId)}`;
}

type MistakeImportJob = BackgroundJob<MistakeItem> & { captureId: string };

function jobResultBelongsToLearner(job: BackgroundJob<MistakeItem>, learnerId: string): boolean {
  if (job.status !== "succeeded") return true;
  return job.result?.learnerId === learnerId;
}

export function MistakeCapture({ onCreated }: MistakeCaptureProps) {
  const learnerId = useLearnerId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [crop, setCrop] = useState<CropSelection>(EMPTY_CROP);
  const [sourceText, setSourceText] = useState("");
  const [originalAnswer, setOriginalAnswer] = useState("");
  const [captureId, setCaptureId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<MistakeImportJob | null>(null);
  const [manualRetries, setManualRetries] = useState(0);
  const handledJobRef = useRef("");
  const persistedLearnerRef = useRef(learnerId);
  const learnerScopeRef = useRef(learnerId);
  const scopeGenerationRef = useRef(0);

  useEffect(() => {
    learnerScopeRef.current = learnerId;
    scopeGenerationRef.current += 1;
    const storageKey = mistakeImportJobStorageKey(learnerId);
    let active = true;
    setJob(null);
    setFile(null);
    setCrop(EMPTY_CROP);
    setSourceText("");
    setOriginalAnswer("");
    setCaptureId("");
    setManualRetries(0);
    setLoading(false);
    setError("");
    handledJobRef.current = "";
    try {
      // The old global key is intentionally never read: it cannot prove which
      // learner owns the job after an identity switch.
      const saved = window.localStorage.getItem(storageKey);
      if (!saved) return () => { active = false; };
      const parsed = JSON.parse(saved) as { learnerId?: string; jobId?: string; captureId?: string; retries?: number };
      if (parsed.learnerId !== learnerId || !parsed.captureId) return () => { active = false; };
      setCaptureId(parsed.captureId);
      setManualRetries(parsed.retries ?? 0);
      if (!parsed.jobId) return () => { active = false; };
      void loadMistakeImportJob(parsed.jobId)
        .then((next) => {
          if (!active || !jobResultBelongsToLearner(next, learnerId)) {
            if (active) window.localStorage.removeItem(storageKey);
            return;
          }
          setJob({ ...next, captureId: parsed.captureId! });
        })
        .catch(() => window.localStorage.removeItem(storageKey));
    } catch {
      // Local storage is optional; the current tab can still finish the job.
    }
    return () => { active = false; };
  }, [learnerId]);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void loadMistakeImportJob(job.jobId)
        .then((next) => {
          if (active && jobResultBelongsToLearner(next, learnerId)) {
            setJob({ ...next, captureId: job.captureId });
          }
        })
        .catch((requestError) => {
          if (active) setError(requestError instanceof Error ? requestError.message : "识别任务状态读取失败");
        });
    }, 900);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [job, learnerId]);

  useEffect(() => {
    if (persistedLearnerRef.current !== learnerId) {
      // The learner changed in the same render as the old job state. Let the
      // restore effect clear it before persisting anything under the new key.
      persistedLearnerRef.current = learnerId;
      return;
    }
    if (!job) return;
    try {
      const storageKey = mistakeImportJobStorageKey(learnerId);
      if (["succeeded", "cancelled"].includes(job.status)) {
        window.localStorage.removeItem(storageKey);
      } else {
        window.localStorage.setItem(storageKey, JSON.stringify({
          learnerId,
          jobId: job.jobId,
          captureId: job.captureId,
          retries: manualRetries,
        }));
      }
    } catch {
      // Local storage is optional.
    }
    if (job.status === "succeeded" && job.result && handledJobRef.current !== job.jobId) {
      handledJobRef.current = job.jobId;
      onCreated(job.result);
    }
  }, [job, learnerId, manualRetries, onCreated]);

  const chooseFile = (nextFile?: File) => {
    if (!nextFile) return;
    if (!IMAGE_MIME_TYPES.has(nextFile.type) && !IMAGE_SUFFIXES.some((suffix) => nextFile.name.toLowerCase().endsWith(suffix))) {
      setError("请上传 JPG、PNG、WebP 或手机拍摄的图片");
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError("错题图片不能超过 10 MB");
      return;
    }
    const sameFile = file
      && file.name === nextFile.name
      && file.size === nextFile.size
      && file.lastModified === nextFile.lastModified;
    const nextCaptureId = sameFile && captureId
      ? captureId
      : globalThis.crypto?.randomUUID?.() ?? `capture-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setFile(nextFile);
    setCaptureId(nextCaptureId);
    try {
      window.localStorage.setItem(mistakeImportJobStorageKey(learnerId), JSON.stringify({
        learnerId,
        captureId: nextCaptureId,
        retries: 0,
      }));
    } catch {
      // Local storage is optional; the in-memory capture id still deduplicates retries in this tab.
    }
    setCrop(EMPTY_CROP);
    setError("");
  };

  const submit = async () => {
    if (!file || loading) return;
    const submitLearnerId = learnerId;
    const submitGeneration = scopeGenerationRef.current;
    const isCurrentScope = () => learnerScopeRef.current === submitLearnerId
      && scopeGenerationRef.current === submitGeneration;
    setLoading(true);
    setError("");
    try {
      const cropped = await cropImageFile(file, crop);
      const stableCaptureId = captureId && job?.status !== "cancelled"
        ? captureId
        : (globalThis.crypto?.randomUUID?.() ?? `capture-${Date.now()}-${Math.random().toString(16).slice(2)}`);
      setCaptureId(stableCaptureId);
      try {
        window.localStorage.setItem(mistakeImportJobStorageKey(learnerId), JSON.stringify({
          learnerId,
          captureId: stableCaptureId,
          retries: manualRetries,
        }));
      } catch {
        // Local storage is optional; retrying in this tab still reuses stableCaptureId.
      }
      const queued = await queueMistakeImport(cropped, stableCaptureId, { sourceText, originalAnswer, learnerId });
      if (!isCurrentScope()) return;
      setManualRetries(0);
      setJob(queued);
    } catch (requestError) {
      if (isCurrentScope()) {
        setError(requestError instanceof Error ? requestError.message : "错题识别失败");
      }
    } finally {
      if (isCurrentScope()) setLoading(false);
    }
  };

  const cancel = async () => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const cancelLearnerId = learnerId;
    const cancelGeneration = scopeGenerationRef.current;
    const isCurrentScope = () => learnerScopeRef.current === cancelLearnerId
      && scopeGenerationRef.current === cancelGeneration;
    try {
      const cancelled = await cancelMistakeImportJob(job.jobId);
      if (!isCurrentScope()) return;
      setJob({ ...cancelled, captureId: job.captureId });
    } catch (requestError) {
      if (isCurrentScope()) {
        setError(requestError instanceof Error ? requestError.message : "取消识别失败");
      }
    }
  };

  const retry = async () => {
    if (!job || job.status !== "failed" || manualRetries >= MAX_MANUAL_RETRIES) return;
    const retryLearnerId = learnerId;
    const retryGeneration = scopeGenerationRef.current;
    const isCurrentScope = () => learnerScopeRef.current === retryLearnerId
      && scopeGenerationRef.current === retryGeneration;
    try {
      const next = await retryMistakeImportJob(job.jobId);
      if (!isCurrentScope()) return;
      setManualRetries((count) => count + 1);
      setJob({ ...next, captureId: job.captureId });
      setError("");
    } catch (requestError) {
      if (isCurrentScope()) {
        setError(requestError instanceof Error ? requestError.message : "重试识别失败");
      }
    }
  };

  return (
    <section className="mistake-capture-page">
      <div className="mistake-section-heading">
        <span className="eyebrow">第 1 步 · 拍照</span>
        <h1>拍下这道错题</h1>
        <p>尽量保持图片清晰、平整，只保留一道完整题目和必要题图。</p>
      </div>

      <input
        ref={inputRef}
        className="visually-hidden"
        type="file"
        accept="image/*,.heic,.heif"
        capture="environment"
        onChange={(event) => chooseFile(event.target.files?.[0])}
        aria-label="选择错题图片"
      />

      {!file ? (
        <button className="mistake-photo-picker" onClick={() => inputRef.current?.click()}>
          <span className="photo-frame" aria-hidden="true" />
          <strong>拍照或选择图片</strong>
          <small>支持相机、相册、JPG、PNG、WebP，最大 10 MB</small>
        </button>
      ) : (
        <>
          <ImageCropper file={file} selection={crop} onChange={setCrop} />
          <button className="mistake-change-photo" onClick={() => inputRef.current?.click()}>重新选择图片</button>
        </>
      )}

      <section className="mistake-capture-details">
        <label>
          <span>你当时写的答案 <small>可选</small></span>
          <textarea
            value={originalAnswer}
            onChange={(event) => setOriginalAnswer(event.target.value)}
            placeholder="例如：我算出 x = 2，或上传照片后在这里补充步骤"
          />
        </label>
        <label>
          <span>题目文字 <small>可选，用于 OCR 不可用或图片不清晰时</small></span>
          <textarea
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
            placeholder="可以直接粘贴题干；留空时由 OCR 自动识别"
          />
        </label>
      </section>

      {error && <p className="mistake-error" role="alert">{error}</p>}
      {job && (
        <div className="mistake-import-job" role="status" aria-live="polite">
          <strong>
            {job.status === "queued" && "已排队等待识别"}
            {job.status === "running" && "正在识别题目"}
            {job.status === "failed" && "识别失败"}
            {job.status === "cancelled" && "已取消识别"}
          </strong>
          <span>{job.message || `进度 ${job.progress}%`}</span>
          {(job.status === "queued" || job.status === "running") && (
            <button type="button" onClick={() => void cancel()}>取消识别</button>
          )}
          {job.status === "failed" && manualRetries < MAX_MANUAL_RETRIES && (
            <button type="button" onClick={() => void retry()}>重试识别（剩余 {MAX_MANUAL_RETRIES - manualRetries} 次）</button>
          )}
        </div>
      )}
      <button className="mistake-primary-action" disabled={!file || loading || Boolean(job && ["queued", "running"].includes(job.status))} onClick={() => void submit()}>
        {loading ? "正在加入队列…" : "加入识别队列"}
      </button>
      {loading && <p className="mistake-loading-note">OCR 与结构化模型会在后台处理，页面刷新后可恢复任务。</p>}
    </section>
  );
}
