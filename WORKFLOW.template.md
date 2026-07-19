---
tracker:
  kind: "{{ TRACKER_KIND }}"
  project_slug: "{{ TRACKER_PROJECT_SLUG }}"
  required_labels:
    - agent-ready
  active_states:
    - Todo
    - In Progress
    - Rework
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
  polling:
    interval_ms: 10000
workspace:
  root: "{{ WORKSPACE_ROOT }}"
hooks:
  after_create: |
    git clone {{ GITHUB_REPO_URL }} .
  before_remove: |
    true
agent:
  max_concurrent_agents: 1
  max_turns: 8
runner:
  command: "{{ RUNNER_COMMAND }}"
server:
  port: {{ ORCHESTRATOR_PORT }}
---

You are working on issue `{{ issue.identifier }}`.

Issue context:

- Identifier: `{{ issue.identifier }}`
- Title: `{{ issue.title }}`
- Current status: `{{ issue.state }}`
- Labels: `{{ issue.labels }}`
- URL: `{{ issue.url }}`

Description:

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

## Grounded Delivery Framework

This repo uses the Grounded Delivery Framework.

Read first:

- `AGENTS.md`
- `framework/contract.yml`
- `framework/harness.json`
- `framework/gates.yml`
- The issue description and labels

## Lane Routing

Use issue labels to determine output:

- `lane:research`: produce cited artifacts under `docs/research/`.
- `lane:spec`: produce product specs, architecture notes, and acceptance criteria under `docs/spec/`.
- `lane:design`: produce UX/content-model decisions under `docs/decisions/` or `docs/spec/`.
- `lane:code`: implement in project source folders; use branches and PRs.
- `lane:qa`: produce proof packages: install logs, test logs, screenshots, and risks.
- `lane:packaging`: produce README, install docs, license/support notes, and release package notes.
- `lane:sales`: produce sales page copy, marketplace listing drafts, pricing notes, and launch assets.

## Workflow Rules

1. Work only inside the provided workspace copy.
2. Treat the issue tracker as the source of truth for task intent.
3. If the issue is `Todo`, move it to `In Progress` before starting when tracker tools are available.
4. Maintain one persistent workpad comment when tracker tools support comments.
5. Keep the workpad updated with plan, acceptance criteria, validation, blockers, and final proof.
6. For research/spec/design work, commit durable artifacts to the repo when useful.
7. For code/UI work, create a branch and PR. Do not push directly to the default branch.
8. For UI work, include desktop/mobile screenshots when browser tooling is available.
9. If a required secret/auth is missing, move the issue to review with a concise blocker note when tracker tools are available.
10. Move the issue to review only when output and proof are ready.

## Required Harness Commands

Run these before final handoff when applicable:

```bash
scripts/verify-contract
scripts/run-gate all
scripts/collect-proof
```

If a command is not applicable for the project adapter, record that as skipped with a reason.

## Required End-of-Run Format

Final response and workpad must include:

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

