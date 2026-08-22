/** 前端上传限制；必须与后端 HTTP 边界保持一致，前端校验只改善体验，不能替代服务端校验。 */
export const ACCEPTED_TEXTBOOK_FILES = "image/*,application/pdf,.heic,.heif";
export const PDF_CHUNK_SIZE = 5 * 1024 * 1024;
export const IMAGE_MAX_SIZE = 10 * 1024 * 1024;
export const PDF_MAX_SIZE = 500 * 1024 * 1024;
const PDF_TAIL_CHECK_SIZE = 64 * 1024;

export function formatFileSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/**
 * Fail fast in the browser before uploading a large file. This is not a
 * security boundary: UploadRegistry repeats the same envelope checks on the
 * server because browser input is untrusted.
 */
export async function validatePdfEnvelope(file: File) {
  const header = await file.slice(0, 8).text();
  if (!header.startsWith("%PDF-")) {
    throw new Error("这个文件扩展名是 PDF，但文件头无效。请确认它是真正的 PDF 文件。");
  }

  const tailStart = Math.max(0, file.size - PDF_TAIL_CHECK_SIZE);
  const tail = await file.slice(tailStart).text();
  if (!tail.includes("%%EOF")) {
    throw new Error(
      "这个 PDF 没有正常结束标记（%%EOF），文件可能未下载完整或导出中断。请重新下载，或用“打印 → 存储为 PDF”生成新文件后再上传。",
    );
  }
}
