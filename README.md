# Grounded Delivery Framework

A reusable contract, gate, and proof harness for AI-assisted delivery.

The framework is intentionally stack-agnostic. A website, web app, API, CMS, docs repo, Strapi project, Next.js project, Symfony app, Python service, or package can all use the same operating model:

1. Define the work contract.
2. Run stack-specific adapter commands.
3. Evaluate gates.
4. Collect proof.
5. Hand the result to a human with clear approve/rework/choose-next output.

## What This Is

This repo is a starter for a reusable GitHub template. It provides:

- A portable `framework/contract.yml` shape.
- A default four-gate lifecycle in `framework/gates.yml`.
- Stack adapters under `adapters/`.
- A machine-readable `framework/harness.json`.
- A proof schema for agent and CI reports.
- Bash/Python scripts with no third-party runtime dependencies.
- GitHub Actions and Symphony workflow templates.

## What This Is Not

- It is not a Strapi starter.
- It is not a Next.js starter.
- It is not tied to Linear, GitHub, or Symphony, although templates are included for those tools.
- It does not replace real tests, reviews, or approvals.

## Quick Start

Copy this repo as a template into a project, then edit:

```text
framework/contract.yml
framework/harness.json
adapters/<your-stack>.yml
AGENTS.md
WORKFLOW.template.md
```

Run the local harness:

```bash
scripts/verify-contract
scripts/run-gate G1
scripts/collect-proof
```

Use `scripts/run-gate all` to run every configured gate in order.

## Core Model

```text
Issue or task
  -> contract
  -> adapter commands
  -> gates
  -> proof bundle
  -> review decision
```

The contract describes what must be true. The adapter describes how this repo proves it. Gates decide whether the work can move forward. Proof makes the result auditable.

## Repository Layout

```text
framework/
  contract.yml       Human-readable work contract.
  gates.yml          Default gate definitions.
  harness.json       Machine-readable harness config.
  lanes.yml          Output routing by lane.
  proof-schema.json  Proof report schema.
adapters/
  static-site.yml
  web-app.yml
  nextjs.yml
  strapi.yml
  node.yml
  python.yml
  php-symfony.yml
scripts/
  detect-stack
  run-gate
  collect-proof
  verify-contract
  write-report
.github/workflows/
  gates.yml
docs/
  setup.md
  symphony.md
  examples/
examples/
  static-site/
  next-strapi/
```

## Required End-of-Run Format

Every agent or automation run should end with:

```text
Output delivered:
[links/files]

Proof:
[tests/screenshots/citations/checks]

Decision needed:
[approve / rework / choose option]

Recommended next issues:
1. [title] - lane:[lane]
```

## Design Principles

- The framework owns process, not product architecture.
- Adapters are thin command maps.
- Proof is first-class output, not a nice-to-have.
- Claims about the repo must be grounded in file paths, logs, screenshots, or marked assumptions.
- A blocked run is acceptable when the blocker is explicit.
- A green run without proof is not acceptable.

