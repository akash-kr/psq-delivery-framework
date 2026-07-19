# PSQ Delivery Framework

Executable architecture for the process in [`psq-framework-deck.html`](psq-framework-deck.html): one shared contract, four ordered gates, inspectable proof, and automated escalation.

```text
contract signed
      │
      ▼
G1 Build ──▶ G2 Validate ──▶ G3 Backend wired ──▶ G4 Client UAT
 UI + unit      browser +         same journey       approval record
 tests          visual proof      on real backend    + milestone release
      │              │                  │                   │
      └──────── red loops back to the responsible team ─────┘
```

G1–G3 are internal CI gates. G4 is intentionally separate: it runs only after the client UAT record is approved. A green G4 writes `.grounding/milestone-release.json` with `status: ready_for_invoice`.

## Contract

[`framework/contract.yml`](framework/contract.yml) is the shared Design, Engineering, and QA agreement. It defines scope, data and behavior expectations, acceptance criteria, proof, and change control. Run strict validation in CI:

```bash
scripts/verify-contract --strict
```

Repository controls remain mandatory: protect the default branch, require the internal gate job, require two approvals for contract changes (Design and QA), and restrict the UAT environment/job to authorized approvers. Those forge settings cannot be enforced by files in this repository alone.

## Gates and proof

The harness is configured in [`framework/harness.json`](framework/harness.json), with the human-readable requirements in [`framework/gates.yml`](framework/gates.yml).

```bash
scripts/run-gate G1          # build + unit checks
scripts/run-gate G2          # browser journey + visual comparison
scripts/run-gate G3          # integrated backend journey
scripts/run-gate internal    # G1 through G3, in order
scripts/approve-uat --approved-by "client@example.com" --notes "UAT complete"
scripts/run-gate G4          # requires the protected approval record above
scripts/run-gate all         # full sequence; normally blocks at pending UAT
scripts/collect-proof
```

Each later gate requires every earlier gate to be green for the same source revision. Empty commands fail instead of silently passing. A literal `"skip"` is allowed only as an explicit not-applicable decision; for example, a frontend-only site may skip backend integration.

The deck also requires a Codex review on every merge request. Adapters intentionally leave `ai_review` unconfigured, so G1 stays red until the target repository maps it to its Codex review status/API check. Do not mark this step `"skip"` on delivery projects.

Outputs are generated under `.grounding/`:

- `gate-status.json` — status, steps, failure age, and source revision.
- `logs/` and `screenshots/` — command and visual evidence.
- `uat-approval.json` — protected-job audit record tied to the tested revision.
- `proof-report.json` / `.md` — contract, revision, gates, proof, risks, and milestone readiness.
- `milestone-release.json` — emitted only after all four gates pass.

The JSON report is checked against the required shape in [`framework/proof-schema.json`](framework/proof-schema.json).

## Stack adapters

Adapters translate generic gate checks into project commands without changing the gate engine:

```bash
scripts/detect-stack
scripts/apply-adapter nextjs
scripts/apply-adapter nextjs --force  # replace custom commands intentionally
```

Included adapters: Next.js, Strapi, generic web app, Node.js, Python, Symfony, and static sites. Required npm scripts are not invoked with `--if-present`; a missing test is a failed configuration, not a green check. Package-lock projects use `npm ci` for reproducibility.

After applying an adapter, review every command. Configure project-specific test names, and use `"skip"` only when a gate check genuinely does not apply.

## CI and UAT

- [`.gitlab-ci.yml`](.gitlab-ci.yml) runs strict contract validation and G1–G3, then exposes G4 as a manual `client-uat` environment job.
- [`.github/workflows/gates.yml`](.github/workflows/gates.yml) provides the equivalent portable workflow; G4 runs only by manual dispatch through the protected `client-uat` environment.

Before G4, the protected UAT job runs `scripts/approve-uat` to write a revision-bound approval record. [`framework/uat-approval.example.json`](framework/uat-approval.example.json) documents its shape. The protected environment/job is the authorization boundary; the generated file is the durable pipeline artifact.

## Escalation contract

[`watcher.rules.yml`](watcher.rules.yml) is a versioned operational contract, not just watcher configuration. Strict contract verification validates it before G1. It currently supports:

- `gate_status` — a failed or blocked gate stays red beyond its SLA, with optional per-gate SLAs;
- `gate_deadline` — a gate is not green by an agreed time, including work that never started;
- `contract_approval` — the contract is edited or its latest commit lacks the named approval trailers;
- `milestone_risk` — a milestone enters its warning/critical window while gates are missing or stale;
- `uat_pending` — internal gates are green but client UAT has exceeded its response SLA;
- `artifact_missing` — required proof is absent after a configured gate.

Rules name a Slack channel configuration, severity, responsible people, repeat interval, and condition. Stuck-gate and UAT rules may define `critical_after_hours`; crossing that threshold bypasses the cooldown and immediately sends the higher-severity escalation. The incoming webhook controls the actual Slack destination; `notify` records the intended owners in the message. Multiple channel entries can use different webhook environment variables.

```bash
python3 tools/escalation-watcher.py --validate
python3 tools/escalation-watcher.py --dry-run
python3 tools/escalation-watcher.py
python3 tools/escalation-watcher.py --loop 30
```

Set `SLACK_WEBHOOK_URL` to send Slack alerts. Without it—or if delivery fails—the default channel writes to stdout so cron/CI still sees the alert. The watcher stores active incidents in `.grounding/escalation-state.json`; persist that file between scheduled runs to deduplicate alerts, re-notify after the configured interval, and send a recovery message when the condition clears.

Two time-based rules ship disabled because their dates are project-specific. For each milestone, set and enable `gate-deadline` and `milestone-at-risk`. Contract commits satisfy the default approval rule with distinct trailers:

```text
Approved-by: design
Approved-by: qa
```

Run the watcher from a host that has the latest `.grounding/gate-status.json` and proof artifacts. A long-running process naturally retains them; a CI scheduler must restore both the gate artifacts and escalation state before checking.

The GitHub workflow already runs this check every 30 minutes, restoring the latest completed default-branch proof and the previous incident state. Add `SLACK_WEBHOOK_URL` as a GitHub Actions secret. For GitLab, create a pipeline schedule (for example every 30 minutes) and add the same value as a masked CI/CD variable; the scheduled job reuses the gate artifact and persists state through the GitLab cache.

## Repository layout

```text
framework/   contract, gate definitions, harness, proof schema, UAT record shape
adapters/    stack-specific command maps
scripts/     stdlib-only Python gate engine and thin shell entry points
tools/       escalation watcher
tests/       harness regression tests
```

## License

MIT — see [`LICENSE`](LICENSE).
