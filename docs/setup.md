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

Set it in `framework/harness.json`:

```json
{
  "project": {
    "adapter": "nextjs"
  }
}
```

Then copy the adapter commands into the `commands` section and edit them for the project.

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

Use `.github/workflows/gates.yml` as the default GitHub Actions workflow. It runs the same local harness commands a human or agent would run.

## 6. Wire Symphony

Copy `WORKFLOW.template.md` to `WORKFLOW.md`, replace placeholders, and follow `docs/symphony.md`.

## 7. Decide License And Support

Before publishing a public reusable repo, choose:

- license
- support policy
- contribution policy
- versioning policy

