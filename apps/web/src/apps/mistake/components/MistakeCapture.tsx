import { useRef, useState } from "react";
import { importMistake } from "../../../api/mistakes";
import type { MistakeItem } from "../../../types/index";
import { cropImageFile, ImageCropper, type CropSelection } from "./ImageCropper";

interface MistakeCaptureProps {
  onCreated: (item: MistakeItem) => void;
}

const EMPTY_CROP: CropSelection = { top: 0, right: 0, bottom: 0, left: 0 };
const IMAGE_SUFFIXES = [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"];
const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif", "image/bmp", "image/tiff"]);

export function MistakeCapture({ onCreated }: MistakeCaptureProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [crop, setCrop] = useState<CropSelection>(EMPTY_CROP);
  const [sourceText, setSourceText] = useState("");
  const [originalAnswer, setOriginalAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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
    setFile(nextFile);
    setCrop(EMPTY_CROP);
    setError("");
  };

  const submit = async () => {
    if (!file || loading) return;
    setLoading(true);
    setError("");
    try {
      const cropped = await cropImageFile(file, crop);
      onCreated(await importMistake(cropped, { sourceText, originalAnswer }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "错题识别失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="mistake-capture-page">
      <div className="mistake-section-heading">
        <span className="eyebrow">STEP 01 · CAPTURE</span>
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
      <button className="mistake-primary-action" disabled={!file || loading} onClick={() => void submit()}>
        {loading ? "正在识别题目…" : "识别并进入确认"}
      </button>
      {loading && <p className="mistake-loading-note">OCR 与结构化模型可能需要数秒，请不要重复提交。</p>}
    </section>
  );
}
