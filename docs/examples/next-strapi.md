# Next.js + Strapi Example

Use this for a split frontend/CMS project.

Recommended project shape:

```text
apps/
  web/
  cms/
packages/
  shared/
```

Recommended commands:

```json
{
  "commands": {
    "install": "npm install",
    "lint": "npm run lint --if-present",
    "typecheck": "npm run typecheck --if-present",
    "build": "npm run build",
    "test": "npm test --if-present",
    "screenshot": "npm run test:e2e --if-present"
  }
}
```

Recommended proof:

- CMS build log
- frontend build log
- content model review
- seed data verification
- preview workflow screenshot
- desktop and mobile frontend screenshots

