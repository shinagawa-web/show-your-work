# distroless-ephemeral-debug

This starts a distroless nonroot Pod with a CPU limit in a kind cluster, attaches ephemeral containers with `kubectl debug`, and records what the debugger can and cannot see: namespaces, `/proc/<pid>/...`, cgroup files, capabilities per `--profile`, the limits of ephemeral containers, and a node debugger.

## Run

`.github/workflows/distroless-ephemeral-debug.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`results/summary.md` goes to the job summary, and every raw output under `results/` is uploaded as the `distroless-ephemeral-debug-results` artifact.

## uid 0 targets

`results/uid0/` holds the same probes from the `legacy` and `default` debuggers against `app-uid0` (uid 0, gid 65532) and `app-uid0-gid0` (uid 0, gid 0) in `k8s/app-uid0.yaml`, which differ from `app` only in the user and group they run as.

## Pinned versions

`scripts/versions.env` and the digest in `app/Dockerfile`. The default `--profile` depends on the kubectl version; `00-environment.txt` records the help text of the pinned one.
