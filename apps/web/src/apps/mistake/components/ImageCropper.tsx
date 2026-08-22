import { useEffect, useState } from "react";

export interface CropSelection {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

interface ImageCropperProps {
  file: File;
  selection: CropSelection;
  onChange: (selection: CropSelection) => void;
}

const EDGES: Array<[keyof CropSelection, string]> = [
  ["top", "裁去上方"],
  ["bottom", "裁去下方"],
  ["left", "裁去左侧"],
  ["right", "裁去右侧"],
];

export function ImageCropper({ file, selection, onChange }: ImageCropperProps) {
  const [preview, setPreview] = useState("");

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const setEdge = (edge: keyof CropSelection, value: number) => {
    const opposite = edge === "top" ? "bottom" : edge === "bottom" ? "top" : edge === "left" ? "right" : "left";
    onChange({ ...selection, [edge]: Math.min(value, 80 - selection[opposite]) });
  };

  return (
    <section className="mistake-cropper" aria-label="裁切错题图片">
      <div className="crop-preview">
        <div className="crop-image-stage">
          {preview && <img src={preview} alt="待识别错题预览" />}
          <span
            className="crop-window"
            style={{
              top: `${selection.top}%`,
              right: `${selection.right}%`,
              bottom: `${selection.bottom}%`,
              left: `${selection.left}%`,
            }}
          />
        </div>
      </div>
      <div className="crop-controls">
        <div>
          <strong>调整识别范围</strong>
          <small>把题目之外的书页、桌面或其他题裁掉，可以提高识别准确率。</small>
        </div>
        {EDGES.map(([edge, label]) => (
          <label key={edge}>
            <span>{label} {selection[edge]}%</span>
            <input
              type="range"
              min="0"
              max="45"
              value={selection[edge]}
              onChange={(event) => setEdge(edge, Number(event.target.value))}
              aria-label={label}
            />
          </label>
        ))}
      </div>
    </section>
  );
}

export async function cropImageFile(file: File, selection: CropSelection): Promise<File> {
  if (Object.values(selection).every((value) => value === 0)) return file;
  const bitmap = await createImageBitmap(file);
  const sourceX = Math.round(bitmap.width * selection.left / 100);
  const sourceY = Math.round(bitmap.height * selection.top / 100);
  const sourceWidth = Math.max(1, Math.round(bitmap.width * (100 - selection.left - selection.right) / 100));
  const sourceHeight = Math.max(1, Math.round(bitmap.height * (100 - selection.top - selection.bottom) / 100));
  const canvas = document.createElement("canvas");
  canvas.width = sourceWidth;
  canvas.height = sourceHeight;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("浏览器无法创建图片裁切画布");
  context.drawImage(bitmap, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, sourceWidth, sourceHeight);
  bitmap.close();
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((result) => result ? resolve(result) : reject(new Error("图片裁切失败")), "image/jpeg", 0.9);
  });
  const stem = file.name.replace(/\.[^.]+$/, "") || "mistake";
  return new File([blob], `${stem}-cropped.jpg`, { type: "image/jpeg" });
}
