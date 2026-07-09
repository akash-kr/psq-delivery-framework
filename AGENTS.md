# Agent Instructions

This repository uses the Grounded Delivery Framework.

## Operating Model

- The issue tracker is the source of truth for task intent.
- The repository is the source of truth for code, docs, contracts, proof, and review history.
- The harness is the source of truth for gates.
- Human approval is required for scope changes, secrets, paid services, and release decisions.

## Grounding Rules

Before making claims or changes, inspect the relevant files.

When reporting work:

- Cite changed files.
- Cite commands run.
- Include logs, screenshots, citations, or proof paths when relevant.
- Mark uncertain statements as assumptions.
- Do not mark work complete if required proof is missing.

## Lane Routing

Use labels or issue metadata to decide output:

- `lane:research`: cited research under `docs/research/`.
- `lane:spec`: product specs, architecture notes, and acceptance criteria under `docs/spec/`.
- `lane:design`: UX, content model, interaction, or visual decisions under `docs/decisions/` or `docs/spec/`.
- `lane:code`: implementation under project source folders, with tests and PR proof.
- `lane:qa`: proof packages, test logs, screenshots, install logs, and risks.
- `lane:packaging`: README, setup, license, support, and release docs.
- `lane:sales`: listing copy, pricing notes, launch copy, and sales assets.

## Gate Rules

Run the relevant gate before moving work forward:

```bash
scripts/run-gate G1
scripts/run-gate G2
scripts/run-gate G3
scripts/run-gate G4
```

Use `scripts/run-gate all` when preparing review.

## End-of-Run Format

Every run must end with:

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

## Constraints

- Do not add paid services or paid API dependencies without explicit approval.
- Do not create unrelated refactors.
- Do not push directly to the default branch unless explicitly instructed.
- For UI work, produce desktop and mobile proof when browser tooling is available.
- For research, cite sources and record confidence/unknowns.

