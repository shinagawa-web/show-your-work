---
name: check-design
description: Checks a verification's first runs against its plan. Use when asked to check a verification against an issue or plan.
argument-hint: <issue URL> <PR URL>
---

1. From the PR, find the verification folder it changes and the latest run of that folder's workflow on the PR's branch. Do not read the issue. Stop if there is not exactly one folder or no run
2. Start blind-reader with only the slug, the run's commit and the run ID
3. Start design-reviewer with the issue, the slug, commit, run and blind-reader's output
4. Return design-reviewer's findings in the user's language
