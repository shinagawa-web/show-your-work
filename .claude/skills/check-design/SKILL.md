---
name: check-design
description: Checks a verification's first runs against its plan. Use when asked to check a verification against an issue or plan.
argument-hint: <issue URL or plan path> <slug> <run IDs>
---

1. Take the commit from the latest run (`gh run view <id> --json headSha`). Do not read the plan
2. Start blind-reader with only `Slug`, `Commit` and `Runs`
3. Start design-reviewer with the plan (`gh issue view <url> --comments`, or the path), the slug, commit, runs and blind-reader's output
4. Return design-reviewer's findings in the user's language
