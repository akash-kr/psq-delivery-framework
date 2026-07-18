# Autonomous Delivery Framework — Architecture v2

Owner: Akash · Status: Draft for review · Date: 2026-07-09

The goal: **you think, agents execute, machinery verifies.** One line of intent should be enough to
produce anything from a single HTML file to a production app — with proof, and nothing merged
without your review.

This document upgrades the existing Grounded Delivery Framework (contract → adapters → gates →
proof) from a *process spec* into an *operating system*: it adds the orchestration, steering, and
enforcement layers that the current repo lacks.

---

## 0. Design Verdict on the Current Repo

Keep. The skeleton is right. What exists today and survives unchanged in spirit:

| Existing asset | Verdict |
|---|---|
| `framework/contract.yml` | Keep. Becomes the per-project work contract, generated (not hand-written) by the Scoper. |
| `framework/gates.yml` (G0–G4) | Keep. Becomes the per-issue lifecycle inside the agent loop. |
| `adapters/*.yml` | Keep. This is your app-agnosticism — thin command maps per stack. |
| `framework/proof-schema.json` + `.grounding/` | Keep. Proof stays first-class. |
| `AGENTS.md` end-of-run format | Keep. Becomes the required PR body format. |
| `scripts/run-gate`, `collect-proof`, `verify-contract` | Keep. These become the harness CLI the daemon calls. |

What's missing (and what this doc adds): an orchestrator, a steering/scoping layer, *enforced*
guardrails (vs. prompted ones), an eval ring, and a preview/demo surface.

---

## 1. The Seven Layers

```
 L0  STEERING      you + Scoper agent        idea → contract → Linear issues
 L1  CONTROL PLANE Linear                    states, lanes, blocked-by, decisions
 L2  ORCHESTRATOR  Symphony-conformant daemon (Mac mini → VM)
 L3  RUNNERS       Claude Code headless / Codex / Hermes (pluggable)
 L4  GUARDRAILS    enforced (hooks, branch protection) + prompted (rules file)
 L5  HARNESS       G0–G4 gate loop, atomic commits, proof bundle, PR
 L6  EVALS         deterministic CI → adversarial eval agent → you
 L7  PROOF SURFACE preview deploys, demos, proof bundles in PRs
```

Everything below main is disposable; everything on main passed through you. That's the invariant.

---

## 2. L0 — Steering: "I only want to think"

This is the new ambition, so it gets the most design attention.

### 2.1 The Scoper

A dedicated agent role (a Claude Code skill or slash command, run interactively) that turns a
one-paragraph idea into an executable plan. It is the *only* agent you talk to at length.

Input: your idea, in as few words as you like.
Output, in order:

1. **Clarification round — capped.** Max 5 questions, multiple-choice where possible, one round
   only. Anything unresolved becomes a recorded assumption in the contract, not a blocker.
2. **`framework/contract.yml`** — filled, not template. Objective, scope in/out, assumptions,
   acceptance criteria each with a named proof.
3. **Linear project + issues.** Epics → issues, each issue carrying:
   - lane label (`lane:research|spec|design|code|qa|packaging`)
   - acceptance criteria (checklist in description)
   - required proof (what evidence closes it)
   - `blocked-by` relations (this is the dependency graph the daemon respects)
   - size guess (S/M/L) so you can sanity-check the plan in 30 seconds
4. **A "Plan Review" issue assigned to you.** Nothing dispatches until you move it to Approved.

Sizing rule the Scoper must obey: **every issue must be completable in one agent session and
reviewable in under 10 minutes.** If it isn't, split it. This single rule is what makes the rest of
the system work — small issues → small PRs → atomic commits → fast reviews → real steering.

### 2.2 Your decision surface

You interact with the system in exactly three places, nowhere else:

1. **Plan approval** — approve/edit the Scoper's issue breakdown (once per project/milestone).
2. **Blocker answers** — agents that hit a genuine decision post a `Decision needed:` comment on
   the issue and move it to `Blocked`. Fixed format (already defined in AGENTS.md).
3. **PR review** — proof bundle + preview link + diff. Approve, or comment `rework: <what>` which
   sends the issue back to `Ready` with your comment as new context.

Optional: a daily digest (scheduled task) that summarizes: PRs awaiting you, blocked issues,
what merged, what's in flight. Target steady state: **~15 min/day per active project.**

---

## 3. L1 — Control Plane: Linear

Linear is the source of truth for *intent and state*. The repo is the source of truth for
*code and proof*. The daemon is the source of truth for *dispatch*.

### 3.1 State machine

```
Backlog → Scoped → Ready → In Progress → Gate Check → Human Review → Approved → Done
                      ↑                                    |
                      └────────── rework ──────────────────┘
        Blocked (from any active state; carries a Decision-needed comment)
```

- `Ready` is the daemon's dispatch queue. Only unblocked `Ready` issues get agents.
- `Gate Check` = agent finished, CI + eval agent running.
- `Human Review` = PR open with proof bundle. **Terminal state for agents.** Only you move
  things past it (via PR approval; a webhook/action syncs merge → Done).
- Priority ordering + `blocked-by` edges give the daemon its scheduling for free.

### 3.2 Lanes

Lanes (labels) route output type, exactly as the current `lanes.yml` defines: research produces
cited docs, spec produces acceptance criteria, code produces PRs with tests, qa produces proof
packages. An agent's WORKFLOW prompt branches on lane. This lets the same machinery ship
research memos and production code.

---

## 4. L2 — Orchestrator: the Daemon

Conform to the Symphony spec (openai/symphony, Apr 2026) rather than inventing a scheduler. It's
a small, well-specified loop: poll tracker → dispatch eligible issues to bounded-concurrency
agent sessions → stream events → reconcile → retry with backoff → stop runs whose issues changed
state. No database; tracker + filesystem are the recovery state.

Deployment path:
- **Phase A: Mac mini.** Daemon + 2–3 concurrent sessions. Always on, zero cloud cost, easy to
  observe (tmux/log tail).
- **Phase B: VM.** Same daemon, more concurrency, projects that need to run 24/7 or need Linux
  parity with production. Mac mini stays as your review/preview box or a second runner pool.

### 4.1 Workspaces without worktrees

Your instinct is right and Symphony agrees: **no worktrees.** Each issue gets a plain directory
with a fresh shallow clone:

```
~/factory/work/<PROJECT>/<ISSUE-KEY>/repo     ← fresh clone, branch feat/<ISSUE-KEY>
~/factory/work/<PROJECT>/<ISSUE-KEY>/proof    ← gate logs, screenshots, eval reports
```

- Branch is pushed to origin; the PR is the only artifact that matters.
- Workspace is deleted when the issue reaches a terminal state (daemon cleanup hook).
- Two agents never share a checkout → no branch juggling, no worktree bookkeeping, and a
  destructive agent mistake is confined to a disposable clone. Merge conflicts are handled where
  they belong: at PR rebase time, by an agent told to rebase.

### 4.2 WORKFLOW.md

Per Symphony: each project repo owns a `WORKFLOW.md` — YAML front matter (states, concurrency,
runner exe, timeouts, workspace hooks) + the prompt body that tells the agent how to behave.
Your `WORKFLOW.template.md` becomes this file. The framework repo ships the template; each
project versions its own copy. Process is code, reviewed like code.

---

## 5. L3 — Runners: which agent executes

Recommendation, with reasoning:

| Runner | Role | Why |
|---|---|---|
| **Claude Code headless** (`claude -p`) | **Primary** | Hooks give you *enforceable* guardrails (a PreToolUse hook can hard-block `git push --force` — a prompt can only ask nicely). Skills give you reusable roles (Scoper, Reviewer). Subagents give you cheap fan-out. |
| **Codex app-server** | Secondary | Symphony's reference implementation targets it natively — useful if you want to run the reference daemon unmodified while you build. Also a genuinely different model for the eval ring (see L6). |
| **Hermes** | Pluggable | Wrap it behind the same runner contract. Good candidate for narrow lanes (research, QA sweeps) where you control its harness fully. |

Design rule: the daemon talks to runners through one thin contract — `start(workspace, prompt) →
event stream → exit status`. Runner choice becomes a per-project (or per-lane) config value in
WORKFLOW.md, not an architectural commitment. Practical starting point: **run the Symphony
reference daemon with Codex to get moving this week, and make Claude Code the first runner you
add** — its hook system is what makes L4 enforceable rather than aspirational.

---

## 6. L4 — Guardrails: prompted vs. enforced

The most important idea in this layer: **a rule that only lives in a prompt is a suggestion.**
Split every rule into the tier that can actually hold it.

### 6.1 Enforced (machinery — agent cannot bypass)

| Rule | Enforcement |
|---|---|
| Nothing merges without you | GitHub branch protection on `main`: required review from you, required status checks, no admin bypass, force-push disabled. This alone satisfies "nothing ever merged without me." |
| No destructive git | Claude Code PreToolUse hook + git wrapper in the workspace image: block `push --force`, `reset --hard`, `clean -fd`, `checkout` to older commits, `commit --amend`, any push to `main`. |
| No secrets exposure | `.env*` never in the clone (daemon injects only whitelisted env); commit-time secret scan (gitleaks) as a required check. |
| Blast radius | Agent's credentials are a deploy key scoped to the one repo, push-only-to-`feat/*`. Workspace is a disposable directory; worst case is deleted and re-cloned. |
| Spend/time caps | Daemon-level per-session timeout, per-day session budget, token budget per issue (Symphony config). |
| Green-without-proof | CI job fails the PR if `.grounding/proof/<ISSUE-KEY>/` is missing or doesn't validate against `proof-schema.json`. |

### 6.2 Prompted (rules file — steipete-derived, the durable subset)

Lives in `AGENTS.md` / WORKFLOW prompt body:

- **Atomic commits**: commit only files you touched, list paths explicitly
  (`git commit -m "msg" -- path1 path2`). One logical change per commit. Never amend.
- Never delete files to silence lint/type errors; stop and record a blocker.
- `git status` check before every commit; quote bracketed paths.
- No unrelated refactors; no new paid dependencies without approval.
- Mark uncertainty as assumptions; never claim done without proof.
- End every run in the fixed Output/Proof/Decision/Next format.

Atomic commits matter here beyond hygiene: they make your review *fast* (each commit tells one
story) and make partial rejection possible (revert one commit, keep the rest).

---

## 7. L5 — Harness: the per-issue loop

What one agent session does, start to finish:

```
 1. READ      issue + contract + WORKFLOW prompt + repo conventions
 2. G0 SCOPE  restate task, acceptance criteria, proof plan as first commit
              (docs/plan/<ISSUE-KEY>.md) — cheap to audit later
 3. BUILD     smallest slices, atomic commit per slice, gates run per slice
 4. G1/G2     adapter commands: install/lint/typecheck/build/test — locally, every slice
 5. G3 PROOF  collect-proof → .grounding/proof/<ISSUE-KEY>/: gate logs, test output,
              screenshots (playwright for UI, desktop+mobile), citations for research lanes
 6. G4 PR     open PR: fixed body format, proof bundle committed, preview link,
              "Decision needed", recommended next issues
 7. HANDOFF   Linear → Human Review. Agent stops. Always.
```

Failure handling: gate fails twice on the same cause → stop, write blocker, move to `Blocked`.
No thrash loops burning tokens against a wall — the daemon's retry budget is for transient
failures, not for "try harder."

Adapters keep this stack-agnostic: the loop above never names npm or pip. `detect-stack` picks
the adapter; the adapter maps G1/G2 to real commands. Adding WordPress or Go support = writing
one small YAML file. The one-HTML-file case and the production-app case run the *same loop* —
the adapter and contract are just smaller.

---

## 8. L6 — Evals: three rings, increasing cost

```
 Ring 1  DETERMINISTIC   CI on the PR (GitHub Actions): install, lint, typecheck, build,
                         tests, proof-bundle validation. Clean machine — agent can't fake
                         its environment. Required status check.

 Ring 2  ADVERSARIAL     Eval agent (different model than the builder — e.g. Codex checks
                         Claude's work, or vice versa) runs on the PR:
                         • each acceptance criterion → met? cite file/line/test as evidence
                         • diff scan: scope creep, deleted tests, suspicious changes,
                           hardcoded values pretending to be logic
                         • UI lanes: replay screenshots, compare against criteria
                         Posts a structured verdict comment: pass / fail per criterion.
                         Advisory gate — a fail sends the issue back to Ready with the
                         verdict as context, before spending your attention.

 Ring 3  YOU             Proof bundle + eval verdict + preview link + small diff.
                         The system's job is to make this ring take <10 minutes.
```

Cross-model review in Ring 2 is the cheap trick with outsized value: models are much better at
finding flaws in work they didn't produce, and a different model family doesn't share the
builder's blind spots.

### Where Crabbox fits (honest take)

Crabbox = lease a fast cloud box, rsync your dirty checkout, run the suite remotely. It is
**not** a review or proof tool — it's an execution substrate. For your system it's an optional
Ring-1 accelerator: when a project's test suite gets slow, agents run `crabbox run -- <gate>`
mid-loop instead of grinding the Mac mini, and CI stays the authoritative check. Skip it until a
suite is slow enough to hurt; plain GitHub Actions on the PR already gives you the
clean-machine guarantee. Your demo-verification need is served by Ring 2 + preview deploys, not
by Crabbox.

---

## 9. L7 — Proof Surface: demos you can click

Reviewing a diff is not reviewing a product. Every UI-bearing PR must carry a live preview:

- Static/HTML → Cloudflare Pages or GitHub Pages per-branch preview (free, zero infra).
- Node/Next/API → per-branch preview URL via docker-compose on the Mac mini
  (`<issue-key>.preview.local` behind Caddy) or a PaaS preview env (Vercel/Fly) per project.
- Non-UI lanes → proof bundle is the demo: test logs, cited research, before/after outputs.

The adapter's `package`/`preview` command owns this per stack. PR body links it. You review
product first, code second.

---

## 10. Scaling Story: one file → production app

Same machinery, different contract weight:

| | One HTML file | Production app |
|---|---|---|
| Scoper output | 1 issue, 3 acceptance criteria | Epics, 30 issues, dependency graph |
| Adapter | `static-site` (lint=htmlhint, build=none) | `nextjs` + `strapi`, full commands |
| Gates | G0, G3, G4 (G1/G2 near-empty) | All gates, full CI matrix |
| Evals | Ring 2 screenshot check | All three rings + regression suite |
| Preview | Pages URL | Per-branch compose env |
| Your time | 5 min total | 15 min/day |

Nothing is reconfigured — the contract and adapter *are* the configuration.

---

## 11. Build Order (each phase useful on its own)

**Phase 0 — Enforcement first (a weekend).**
Branch protection on a real repo; guardrail hooks (git wrapper + Claude Code PreToolUse);
AGENTS.md v2 with the atomic-commit rules; proof-validation CI job. Run the G0–G4 loop
*manually* via Claude Code on 2–3 real issues. This validates the harness before any daemon.

**Phase 1 — Linear + Scoper (week 1).**
Linear workspace with the state machine + lanes; build the Scoper as a Claude Code skill; scope
one real project through it; execute issues manually. Validates the steering layer.

**Phase 2 — Daemon on Mac mini (weeks 2–3).**
Deploy the Symphony reference implementation (Codex runner) pointed at Linear. Add the Claude
Code runner behind the runner contract. WORKFLOW.md per project. Now agents work while you sleep.

**Phase 3 — Eval ring + previews (week 4).**
Ring-2 eval agent as a GitHub Action on PRs; preview deploys per adapter; daily digest scheduled
task. Now your review time drops to the target.

**Phase 4 — Scale (when needed).**
Move/extend daemon to the VM; Hermes as a lane runner; Crabbox if a suite gets slow; multiple
projects concurrently.

Anti-goal at every phase: building orchestration before enforcement. A daemon that dispatches
unguarded agents is a faster way to make messes.

---

## 12. Open Questions (deliberately deferred)

1. **Linear ↔ GitHub sync** — webhook service or polling? (Phase 2 decision; polling is fine to start.)
2. **Per-project vs. shared daemon** — start shared, split if projects need different trust levels.
3. **Hermes runner contract** — depends on what Hermes exposes; spec it when Phase 4 nears.
4. **Multi-repo projects** (e.g., Next + Strapi split repos) — contract spans repos; issue carries repo target. Design when it first occurs.
5. **Cost telemetry** — Symphony tracks tokens per session; decide where you want to see it (digest vs. Linear comment).

---

## 13. References

- Symphony spec: github.com/openai/symphony (SPEC.md, Draft v1) — daemon loop, WORKFLOW.md, per-issue workspaces, Human Review handoff.
- Crabbox: github.com/openclaw/crabbox — remote testbox runner; optional Ring-1 substrate.
- steipete's agent git rules (gist d3b9db3…) — atomic commits, no destructive git; absorbed into L4.
- This repo's v1: contract/gates/adapters/proof — absorbed into L5.
