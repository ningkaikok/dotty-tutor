## Conventions (read first)

Dotty Tutor is a Chinese-language math tutoring app for middle-school students. Its visual language is **Bright**: a light lavender page, white rounded cards, purple primary, orange accent, large touch targets. All UI copy is Simplified Chinese.

### Setup

- Link `styles.css` once. It carries the Bright tokens (CSS custom properties on `:root`), the KaTeX styles and the Nunito font. There is no provider component.
- Answering components (`QuestionContent` options, prompts, tables) only get the Bright look **inside an element with class `student-paper-shell`**. Outside it they fall back to a legacy green exam style. Always wrap question UI:

```jsx
<div className="student-paper-shell" style={{ padding: 24, borderRadius: "var(--radius-xl)", background: "var(--c-surface)" }}>
  <QuestionContent blocks={blocks} selectedOption="C" onSelectOption={(label) => setPicked(label)} />
</div>
```

- `EvaluationEvidence` ("为什么这样判") and `MathText`/`RichText` need no wrapper.

### Styling idiom: tokens, not hex

Style your own layout with inline styles or CSS that reads the tokens via `var(--…)`:

| Purpose | Tokens |
| --- | --- |
| Text | `--c-ink` (body/headings), `--c-ink-soft` (secondary), `--c-ink-muted` (meta, smallest allowed) |
| Surfaces | `--c-ground` (page bg), `--c-surface` (cards), `--c-surface-tint` (inset areas), `--c-line`, `--c-line-strong` |
| Primary | `--c-brand`, `--c-brand-strong` (hover), `--c-brand-soft` (selected bg), `--c-on-brand` (secondary text on purple) |
| Accent | `--c-accent` (submit/continue, focus ring), `--c-accent-strong`, `--c-accent-soft` + `--c-on-accent-soft` |
| Status | correct: `--c-success-soft` + `--c-on-success-soft`; wrong / needs fix: `--c-accent-soft` + `--c-on-accent-soft` (never red for students); neutral: `--c-info-soft` + `--c-on-info-soft`; destructive/system error only: `--c-danger`, `--c-danger-soft` |
| Shape | `--radius-sm` 12, `--radius-md` 16 (buttons/inputs), `--radius-lg` 24 (cards), `--radius-xl` 28 (hero/work area), `--radius-pill` |
| Type & touch | `font-family: var(--font-bright)`; `--touch-min` (44px minimum hit target) |

Rules: white text never sits on `--c-accent` (use `--c-ink`). Headings weight 900, buttons 800. No drop shadows; separate layers by white card on lavender ground. Current/active pills use `--c-ink` background with white text.

### Data shapes

- `MathText`/`RichText` take plain text with explicit `$…$` (inline) or `$$…$$` (display) LaTeX. A bare decimal like `$0.25$` is treated as currency text, not math.
- `QuestionContent.blocks` is an ordered list of `text` / `math` / `image` / `table` / `options` blocks sorted by `sourceOrder`; see `QuestionContent.d.ts`.

### Where the truth lives

- `guidelines/docs/design-system.md`: full Bright spec (contrast rules, status colors, migration notes).
- `components/general/<Name>/<Name>.d.ts` and `.prompt.md`: props and examples.
- `_ds_bundle.css`: the shipped app stylesheet (tokens at the top).
