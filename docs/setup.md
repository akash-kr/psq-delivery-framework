# Setup Guide

## 1. Use This As A Template

Create a new repository from this starter or copy these files into an existing project.

## 2. Choose An Adapter

Pick the closest adapter from `adapters/`:

- `static-site`
- `web-app`
- `nextjs`
- `strapi`
- `node`
- `python`
- `php-symfony`

Run `scripts/detect-stack` to see which adapters match the repo, then apply one:

```bash
scripts/apply-adapter nextjs
```

This sets `project.adapter` and fills every empty command in
`framework/harness.json` from the adapter. Commands you have already
customized are kept. Edit the result for the project; mark steps that
genuinely do not apply with the explicit value `"skip"` — a gate where zero
checks ran fails rather than passing vacuously.

## 3. Fill The Contract

Edit `framework/contract.yml`:

- project name
- project type
- objective
- in-scope work
- out-of-scope work
- assumptions
- unknowns
- acceptance criteria
- required proof

## 4. Run The Harness

```bash
scripts/verify-contract
scripts/run-gate all
scripts/collect-proof
```

## 5. Wire CI

Use `.github/workflows/gates.yml` (GitHub Actions) or `.gitlab-ci.yml` (GitLab) — both run the same local harness commands a human or agent would run. Enable branch protection with required status checks and "Require review from Code Owners" so `.github/CODEOWNERS` actually enforces contract sign-off.

## 6. Wire Escalations

Edit thresholds in `watcher.rules.yml`, then schedule the watcher (cron, launchd, or a CI schedule):

```bash
python3 tools/escalation-watcher.py            # one check
python3 tools/escalation-watcher.py --loop 30  # every 30 minutes
```

Set `SLACK_WEBHOOK_URL` to alert Slack; without it, alerts print to stdout.

## 7. Wire An Orchestrator (Optional)

If agents work issues from a tracker via an orchestrator daemon, copy `WORKFLOW.template.md` to `WORKFLOW.md` and replace the `{{ ... }}` placeholders (tracker kind, workspace root, runner command, port). Secrets go in `.env` (see `.env.example`); the workflow body already routes lanes and enforces the end-of-run format.

## 8. Decide License And Support

Before publishing a public reusable repo, choose:

- license
- support policy
- contribution policy
- versioning policy

