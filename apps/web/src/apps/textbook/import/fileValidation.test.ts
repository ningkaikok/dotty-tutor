import { describe, expect, it } from "vitest";
import { formatFileSize, isPdf, validatePdfEnvelope } from "./fileValidation";

function pdfFile(bytes: number[], name = "test.pdf"): File {
  return new File([new Uint8Array(bytes)], name, { type: "application/pdf" });
}

describe("formatFileSize", () => {
  it("小于 1MB 用 KB 且至少显示 1 KB", () => {
    expect(formatFileSize(0)).toBe("1 KB");
    expect(formatFileSize(512 * 1024)).toBe("512 KB");
  });

  it("1MB 以上用 MB 保留一位小数", () => {
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(2.5 * 1024 * 1024)).toBe("2.5 MB");
  });
});

describe("isPdf", () => {
  it("按 MIME 类型识别", () => {
    expect(isPdf(pdfFile([], "photo.jpg"))).toBe(true);
  });

  it("按扩展名兜底识别（浏览器可能给空 MIME）", () => {
    const file = new File([new Uint8Array([1])], "试卷.PDF");
    expect(isPdf(file)).toBe(true);
    expect(isPdf(new File([new Uint8Array([1])], "image.png"))).toBe(false);
  });
});

describe("validatePdfEnvelope", () => {
  const header = [0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]; // %PDF-1.7
  const eof = [...new TextEncoder().encode("\n%%EOF\n")];

  it("完整信封（头 + 尾标记）通过", async () => {
    await expect(validatePdfEnvelope(pdfFile([...header, ...eof]))).resolves.toBeUndefined();
  });

  it("文件头无效时快速失败并给出中文指引", async () => {
    const fake = pdfFile([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0]); // zip 魔数
    await expect(validatePdfEnvelope(fake)).rejects.toThrow("文件头无效");
  });

  it("缺少 %%EOF 结束标记时提示重新下载", async () => {
    const truncated = pdfFile([...header]);
    await expect(validatePdfEnvelope(truncated)).rejects.toThrow("%%EOF");
  });
});
