# show-your-work

This repository is public. Verification code here often starts from issues in private repositories, but nothing from them may appear in files, commit messages, PR titles, or PR bodies: no links, no `owner/repo#N` references, no repository names, no client or internal details. Describe what the verification reproduces instead.

`scripts/check-public-refs.sh` checks commits for references to repositories that are not publicly reachable. It runs as a pre-push hook once `git config core.hooksPath .githooks` is set, and in CI. It does not see PR titles or bodies, so check those yourself before `gh pr create`.
