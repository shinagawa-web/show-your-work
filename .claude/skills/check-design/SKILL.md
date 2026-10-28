---
name: check-design
description: Checks a verification's first runs against its plan. Use when asked to check a verification against an issue or plan.
argument-hint: <issue URL> <PR URL>
---

1. From the PR, take the slug and the latest run of its workflow. Do not read the issue

       gh pr view <PR URL> --json files,headRefName -q '(.files[].path | split("/")[0] | select(startswith(".") | not)), "branch " + .headRefName' | sort -u
       gh run list --workflow <slug>.yml --branch <branch> --limit 1 --json databaseId,headSha

   Stop if the PR touches more than one folder or the workflow has no run
2. Start blind-reader with only `Slug`, `Commit` and `Runs`
3. Start design-reviewer with the issue (`gh issue view <url> --comments`), the slug, commit, run and blind-reader's output
4. Return design-reviewer's findings in the user's language
