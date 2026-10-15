---
name: check-article
description: Checks an article against its verification before release. Runs blind-reader without the article, then evidence-reviewer with the article and blind-reader's output. Use when asked to check or review an article that links to a verification in this repository.
argument-hint: <path to the article>
---

## 1. Read the inputs from the article's links

Take them from the links only, without reading the article's text:

    grep -oE 'github\.com/shinagawa-web/show-your-work/(tree|blob)/[0-9a-f]{7,40}/[A-Za-z0-9_.-]+' "$ARGUMENTS" | sort -u
    grep -oE 'actions/runs/[0-9]+' "$ARGUMENTS" | sort -u

The first gives the commit and the slug, the second the run IDs. If the article links to more than one commit or slug, or to no run, stop and say so.

## 2. Run blind-reader

Start the blind-reader agent with this prompt, filled in and nothing else:

    Slug: <slug>
    Commit: <commit>
    Runs: <run IDs, comma separated>

Do not add the article's path, its title, its claims or what the experiment is expected to show.

## 3. Run evidence-reviewer

After blind-reader returns, start the evidence-reviewer agent with:

- The article's path
- The slug, the commit and the run IDs
- blind-reader's output, in full

## 4. Report

Return evidence-reviewer's output as it is. Add the statements blind-reader lists under "What it cannot show" that evidence-reviewer did not take up.
