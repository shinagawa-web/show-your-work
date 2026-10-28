---
name: check-design
description: Checks a verification against its plan after its first runs, while the design can still change. Runs blind-reader without the plan, then design-reviewer with the plan and blind-reader's output. Use when asked to check or review a verification in this repository against an issue or plan, before the article is written.
argument-hint: <issue URL or path to the plan> <slug> <run IDs>
---

## 1. Collect the inputs

From the arguments, take the plan (an issue URL or a file path), the slug and the run IDs. Ask for any that are missing. Do not read the plan yet.

Take the commit each run ran:

    gh run view <id> --json headSha -q .headSha

Use the commit of the latest run. Stop and say so if a commit is not found after `git fetch origin`.

## 2. Run blind-reader

Start the blind-reader agent with this prompt, filled in and nothing else:

    Slug: <slug>
    Commit: <commit>
    Runs: <run IDs, comma separated>

Do not add the plan, its title, its claims or what the experiment is expected to show.

## 3. Run design-reviewer

After blind-reader returns, start the design-reviewer agent with:

- The plan: the issue URL, to be read with `gh issue view <url> --comments`, or the file path
- The slug, the commit and the run IDs
- blind-reader's output, in full

## 4. Report

Return design-reviewer's output without changing its content or structure, written in the language the user writes in. Keep code, log lines and quotes from the plan untouched. Add the statements blind-reader lists under "What it cannot show" that design-reviewer did not take up.
