import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// 脚本位于 <root>/apps/web/scripts，仓库根需要上跳两级。
const repositoryRoot = resolve(frontendRoot, "..", "..");
const outputPath = resolve(frontendRoot, "src/types/generated/api.ts");
const checkOnly = process.argv.includes("--check");
const temporaryDirectory = mkdtempSync(join(tmpdir(), "dotty-tutor-openapi-"));
const openapiPath = join(temporaryDirectory, "openapi.json");
const generatedPath = join(temporaryDirectory, "api.ts");

function pythonExecutable() {
  const candidates = [
    process.env.PYTHON,
    resolve(repositoryRoot, ".venv/bin/python"),
    resolve(repositoryRoot, "apps/api/.venv/bin/python"),
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate)) || "python3";
}

function generateWithOpenapiTypescript() {
  const argumentsForCli = [openapiPath, "--output", generatedPath, "--alphabetize"];
  // openapi-typescript 7 currently declares TypeScript 5 as its peer. npm ci
  // installs that compiler at the workspace root, so the generator can stay
  // deterministic without silently downloading a second runtime during checks.
  const cliPath = resolve(frontendRoot, "node_modules/.bin/openapi-typescript");
  if (!existsSync(cliPath)) {
    throw new Error("缺少 openapi-typescript，请先在 frontend 目录执行 npm ci；生成脚本不会联网临时下载依赖。");
  }
  const compatibleCompiler = resolve(frontendRoot, "node_modules/typescript/lib/typescript.js");
  if (!existsSync(compatibleCompiler)) {
    throw new Error(
      "openapi-typescript 需要 TypeScript 5 compiler API，但未找到兼容运行时；请重新执行 npm ci。",
    );
  }
  execFileSync(cliPath, argumentsForCli, {
    cwd: frontendRoot,
    stdio: "inherit",
  });
}

try {
  execFileSync(pythonExecutable(), [
    resolve(repositoryRoot, "scripts/export-openapi.py"),
    openapiPath,
  ], {
    cwd: repositoryRoot,
    env: { ...process.env, PYTHONPATH: resolve(repositoryRoot, "apps/api") },
    stdio: "inherit",
  });

  generateWithOpenapiTypescript();

  const generated = readFileSync(generatedPath, "utf8");
  if (checkOnly) {
    if (!existsSync(outputPath) || readFileSync(outputPath, "utf8") !== generated) {
      console.error("OpenAPI types are stale. Run `npm run generate:api` and review the result.");
      process.exitCode = 1;
    } else {
      console.log("OpenAPI types are up to date.");
    }
  } else {
    writeFileSync(outputPath, generated, "utf8");
    console.log(`Generated ${outputPath}`);
  }
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
