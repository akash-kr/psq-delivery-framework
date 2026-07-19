# Grounded Delivery Framework

One contract up front. A gate at every step. Proof instead of opinion.

Design, Dev, and QA agree on a single spec before work starts. Every piece of
work then passes through five pass/fail gates decided by tools — nothing moves
forward on someone's say-so, and nothing reaches review without evidence a
reviewer can inspect. The framework is stack-agnostic: a one-file static site
and a Next.js + Strapi build run the exact same loop, with adapters mapping
the gates to real commands.

```text
contract  →  G0 scope  →  G1 build  →  G2 behavior  →  G3 proof  →  G4 review-ready
                │             │             │              │             │
           spec signed   install/lint   tests + visual   evidence     validated
                          typecheck      screenshots     collected    report + PR
```

A gate where **zero checks ran fails** (exit 3) instead of passing vacuously.
Steps that genuinely do not apply to a stack are marked with the explicit
command value `"skip"` — a recorded decision, not a silent gap.

## Quick Start

Copy the template into a project, wire the adapter, run the gates:

```bash
cp -r framework/ adapters/ scripts/ watcher.rules.yml AGENTS.md  <your-repo>/
cd <your-repo>

scripts/detect-stack              # suggests matching adapters (e.g. "nextjs")
scripts/apply-adapter nextjs      # fills empty commands in framework/harness.json
$EDITOR framework/contract.yml    # objective, scope, acceptance criteria + proof

scripts/run-gate all              # the whole loop, in order
scripts/collect-proof             # evidence bundle for the reviewer
```

`scripts/verify-contract --strict` additionally fails on any placeholder
`unset` value left in the contract.

## The Workflow, End to End

**1. Sign the contract.** `framework/contract.yml` is the single source of
truth, read three ways: Design freezes screens and states, Dev gets the data
shapes, QA gets the pass/fail rules. Each acceptance criterion names the proof
that closes it ("card shows price → test checks for `.price`"). It lives in
Git; `.github/CODEOWNERS` plus branch protection means it cannot change
without the right people approving, and commits touching it carry an
`Approved-by:` trailer.

**2. Turn rules into commands.** QA's judgement goes into defining checks;
the harness runs them. In `framework/harness.json`, `lint` might be eslint,
`test` a Playwright suite, `screenshot` a visual-regression run using
Playwright's built-in `toHaveScreenshot()` — see
[docs/visual-proof.md](docs/visual-proof.md).

**3. Run the gates before every push.** A failed contract rule fails the
gate; the tool says no, nobody argues in a review thread. Fix and re-run.
Every command's output lands in `.grounding/logs/`, and per-gate results (with
how long a gate has been red) land in `.grounding/gate-status.json`.

**4. Review evidence, not vibes.** `collect-proof` writes
`.grounding/proof-report.md` (human) and `proof-report.json` (validated
against `framework/proof-schema.json` by the `validate-proof` step in G4):
gate results, changed files, logs, screenshots, and the decision being asked
for. CI — `.github/workflows/gates.yml` or `.gitlab-ci.yml`, same commands —
re-runs everything on a clean machine, so green cannot be faked locally.

**5. Let the watcher chase blockers.** `tools/escalation-watcher.py` runs on
a schedule, reads `watcher.rules.yml`, and shouts when a gate is red past its
SLA, the contract changed without approval, or a milestone is due with gates
not green. Slack first (`SLACK_WEBHOOK_URL`), stdout fallback:

```bash
python3 tools/escalation-watcher.py --loop 30
```

Change a threshold in `watcher.rules.yml`, change the rule — no code.

## Adapters

Adapters are thin YAML command maps that keep the loop stack-agnostic.
Supported out of the box: `static-site`, `web-app`, `nextjs`, `strapi`,
`node`, `python`, `php-symfony`. Adding a stack = one small YAML file with a
`detect:` block and a `commands:` map; `detect-stack` and `apply-adapter`
pick it up automatically.

## Repository Layout

```text
framework/
  contract.yml       The work contract (template — copy and fill per project).
  gates.yml          Gate definitions G0–G4.
  harness.json       Machine-readable config: paths, commands, gate steps.
  lanes.yml          Output routing by lane (research/spec/code/qa/...).
  proof-schema.json  Schema the JSON proof report must satisfy.
adapters/            Stack command maps (thin YAML).
scripts/
  gdf.py             The harness engine (stdlib-only Python).
  detect-stack | apply-adapter | run-gate | verify-contract |
  collect-proof | write-report                     (thin wrappers)
tools/
  escalation-watcher.py   Alerts on red gates, unapproved contract
                          changes, milestone risk (watcher.rules.yml).
  review-server.py        Serves annotatable HTML docs, collects notes.
  annotation-watcher.sh   Turns submitted notes into agent revision runs.
tests/               Harness self-tests (run by gate G2).
watcher.rules.yml    Escalation thresholds — edit values, not code.
.github/workflows/gates.yml   GitHub Actions pipeline.
.gitlab-ci.yml                Same pipeline for GitLab.
.github/CODEOWNERS            Contract changes require owner review.
docs/
  setup.md           Step-by-step project onboarding.
  gates-mapping.md   Deck milestone gates vs. framework issue gates.
  visual-proof.md    How "screenshots match the design" is enforced.
  examples/          Worked examples (static site, Next + Strapi).
examples/            Copyable per-stack harness configurations.
```

## Required End-of-Run Format

Every agent or automation run ends with:

```text
Output delivered:  [links/files]
Proof:             [tests/screenshots/citations/checks]
Decision needed:   [approve / rework / choose option]
Recommended next issues:  1. [title] - lane:[lane]
```

## Design Principles

- The framework owns process, not product architecture.
- Adapters are thin command maps; the gate loop never names npm or pip.
- Proof is first-class output. A green run without proof is not acceptable —
  and a gate that verified nothing is not green.
- A rule that only lives in a prompt is a suggestion; enforcement belongs in
  machinery (gates, CI, CODEOWNERS, the watcher).
- A blocked run is acceptable when the blocker is explicit.

## What This Is Not

Not a project starter for any particular stack, not tied to any particular
issue tracker, forge, or orchestrator, and not a replacement for real tests,
reviews, or approvals — it exists to make those cheap and unavoidable.

## License

MIT — see [LICENSE](LICENSE).
