import { afterEach, describe, expect, it, vi } from "vitest";

describe("API resource origin", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });
  it("user opens an original page from the Render static site then the image uses the API host", async () => {
    vi.stubEnv("VITE_API_ORIGIN", "https://api.example.test/");
    vi.resetModules();
    const { apiResourceUrl } = await import("./client");
    expect(apiResourceUrl("/api/chapters/c1/sources/s1/pages/1/preview")).toBe("https://api.example.test/api/chapters/c1/sources/s1/pages/1/preview");
    expect(apiResourceUrl("data:image/png;base64,fixture")).toBe("data:image/png;base64,fixture");
  });
  it("user reviews a local chapter then its preview keeps the same-origin proxy path", async () => {
    vi.stubEnv("VITE_API_ORIGIN", "");
    vi.resetModules();
    const { apiResourceUrl } = await import("./client");
    expect(apiResourceUrl("/api/chapters/c1/sources/s1/pages/1/preview")).toBe("/api/chapters/c1/sources/s1/pages/1/preview");
  });
});
