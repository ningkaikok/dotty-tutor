# design-sync notes (Dotty Tutor)

## How this repo is synced

- `apps/web` is an app, not a component library: there is no `dist/` and no Storybook. `.design-sync/entry.tsx`
  is the hand-written bundle entry; it re-exports the synced components from `apps/web/src` and imports the
  stylesheets they need. Keep its exports and `componentSrcMap` in config.json in sync.
- Scope is a pilot: `MathText`, `RichText`, `QuestionContent`, `EvaluationEvidence`. Add components by extending
  both the entry and `componentSrcMap`; component props come from `dtsPropsFor` (hand-written), because there is
  no shipped `.d.ts` to extract from. When a component's props change in source, update `dtsPropsFor`.
- Build command: `node .ds-sync/package-build.mjs --config .design-sync/config.json --node-modules apps/web/node_modules --out ./ds-bundle`
  (from repo root, after `pnpm i --frozen-lockfile`). `--node-modules` must be `apps/web/node_modules` (pnpm keeps
  react/katex/@fontsource there, not at the root).
- Render check needs Playwright importable from `.ds-sync/`: `npm i playwright@1.63.0` there (matches the repo's
  `@playwright/test` and the cached `chromium-1243`).

## Gotchas

- KaTeX: `katex/dist/katex.min.css` references `.ttf` files and esbuild has no `.ttf` loader. `.design-sync/tsconfig.json`
  aliases that import to `.design-sync/katex.css`, a copy with `.ttf`/`.woff` sources stripped (woff2 is inlined).
  Regenerate `katex.css` when katex is upgraded (same transform: drop `.ttf`/`.woff` url() sources, rewrite
  `url(fonts/` to `url(../apps/web/node_modules/katex/dist/fonts/`).
- Bright answering styles are scoped under `.student-paper-shell` (see `apps/web/src/apps/student/student.css`).
  `QuestionContent` previews must wrap in that class or they render in the legacy green exam style.
- `$0.25$`-style decimals are deliberately parsed as currency text by `richTextParser.ts`; use a text block for
  plain decimal options in previews.
- `EvaluationEvidence` renders a collapsed `<details>`; previews open it via a ref to show the grading facts.
- Writing previews through a bash heredoc lost LaTeX backslashes (`\b` became a backspace) in an earlier run; write
  preview files with a file-writing tool and double-escape (`"\\frac"`).

## Known render warns

- `[FONT_MISSING] "Inter", "Nunito", "Microsoft YaHei", "Songti SC", "STSong"`: by design. Bright ships
  "Nunito Variable" (via @fontsource, present in the bundle); "Nunito" is only a fallback name in the stack. Chinese
  text uses system fonts (PingFang SC / Microsoft YaHei / Noto Sans SC) on purpose, documented in
  `docs/design-system.md`. Inter and Songti/STSong belong to the legacy (unmigrated) styles in `styles.css`.
  The user explicitly accepted system-font substitutes for these on 2026-10-12 (first sync); do not bundle a CJK webfont.

## Re-sync risks

- `dtsPropsFor` is a hand-maintained copy of component prop types and silently goes stale if source props change.
- `katex.css` is a generated copy tied to katex 0.18.7.
- The synced CSS includes the whole global `styles.css` plus `student.css`, including legacy unmigrated rules; if
  later migrations rename `.student-paper-shell` or move tokens, the conventions header must be revalidated.
- Grades were made at viewport width with the preview `Shell` fixed at 640px; a change to the compact option grid
  in `student.css` changes the `QuestionContent` layout.
