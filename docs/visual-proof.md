# Visual Proof: "Screenshots Match the Design"

The deck promises that a gate goes green only when screenshots match the
design. The framework does not reimplement image diffing — Playwright already
ships a pixel-comparison assertion. The framework's job is to run it at the
right gate and file the evidence.

## How it is wired

1. The adapter's `screenshot` command runs the project's E2E suite
   (e.g. `npm run test:e2e --if-present` in the `nextjs`, `web-app`, and
   `strapi` adapters).
2. `harness.json` includes `screenshot` in gate **G2** (behavior). A failing
   visual comparison fails the command, which fails the gate — the tool
   decides, not opinion.
3. Screenshots the suite writes into `.grounding/screenshots/` are picked up
   by `collect-proof` and listed in the proof report.

## In the project's Playwright suite

Use Playwright's built-in visual assertion; baselines live in the repo and
update only through review:

```ts
await expect(page).toHaveScreenshot('product-card.png', { maxDiffPixelRatio: 0.01 });
```

- First run records the baseline (`--update-snapshots`); commit it.
- Every later run compares against the baseline and fails on drift.
- Design changes update the baseline in the same PR as the change — the
  reviewer sees old vs. new side by side.

For AI-driven, selector-free checks the deck mentions Midscene.js; it runs
inside the same Playwright suite, so nothing in the framework changes.

## Static sites

The `static-site` adapter marks `screenshot: "skip"` by default (an explicit,
recorded decision). If the project warrants visual proof, point the command at
a small Playwright script and the same G2 wiring applies.
