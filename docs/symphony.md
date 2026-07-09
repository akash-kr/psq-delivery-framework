# Symphony Setup

`WORKFLOW.template.md` is a reusable Symphony workflow template. Copy it to `WORKFLOW.md` in a project repo and replace the placeholders.

## Required Values

```text
{{ LINEAR_PROJECT_SLUG }}
{{ WORKSPACE_ROOT }}
{{ GITHUB_REPO_URL }}
{{ CODEX_COMMAND }}
{{ SYMPHONY_PORT }}
```

Example:

```yaml
tracker:
  kind: linear
  project_slug: "my-project-abc123"
workspace:
  root: ~/code/symphony-workspaces/my-project
hooks:
  after_create: |
    git clone git@github.com:org/my-project.git .
codex:
  command: codex app-server
server:
  port: 4040
```

## Environment

The environment file should contain only secrets required by the tracker or project. For Linear:

```text
LINEAR_API_KEY=lin_api_...
```

Do not commit the env file.

## Recommended Loop

1. Create or update an issue.
2. Add `agent-ready` and one lane label.
3. Symphony clones the repo into a workspace.
4. The agent reads `AGENTS.md`, `framework/contract.yml`, and `framework/harness.json`.
5. The agent runs relevant commands and gates.
6. The agent writes proof.
7. The issue gets a final approve/rework/choose-option handoff.

## Making This Project-Specific

Keep `WORKFLOW.template.md` generic. Put project-specific decisions in:

- `WORKFLOW.md`
- `AGENTS.md`
- `framework/contract.yml`
- `framework/harness.json`

