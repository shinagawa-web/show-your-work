# distroless-ephemeral-debug

This starts a distroless nonroot Pod with a CPU limit in a kind cluster, attaches ephemeral containers with `kubectl debug`, and records what the debugger can and cannot see: namespaces, `/proc/<pid>/...`, cgroup files, capabilities per `--profile`, the limits of ephemeral containers, and a node debugger.

## Run

Requirements on the host:

- cgroup v2
- Docker
- `jq`, `curl`, `python3`, `script` (util-linux)

On Ubuntu:

```
sudo apt-get install -y docker.io jq curl python3
sudo usermod -aG docker "$USER"
```

Then log in again and run:

```
cd distroless-ephemeral-debug
./run-all.sh
```

This writes every raw output under `results/` and prints `results/summary.md`.

On macOS, create the VM once with `limactl create --name=distroless-ephemeral-debug distroless-ephemeral-debug/lima.yaml` and `limactl start distroless-ephemeral-debug`, then run `scripts/vm-run.sh distroless-ephemeral-debug` from the repository root. It copies this folder into the VM, runs `./run-all.sh` there and copies `results/` back.

## uid 0 targets

`scripts/uid0.sh` (also called from `run-all.sh`) starts `app-uid0` (uid 0, gid 65532) and `app-uid0-gid0` (uid 0, gid 0) from `k8s/app-uid0.yaml`, which differ from `app` only in `runAsUser`/`runAsGroup`/`runAsNonRoot`, runs `scripts/probe.sh` from `legacy` and `default` debuggers against them, and writes `results/uid0/<pod>-<profile>.txt`. Each file ends with the target's `/proc/<pid>/status` credentials read from the node.

## Pinned versions

`scripts/versions.env` pins kind, the kind node image, kubectl and netshoot. The app is built on `gcr.io/distroless/static-debian12:nonroot` pinned by digest in `app/Dockerfile`. The default `--profile` depends on the kubectl version; `00-environment.txt` records the help text of the pinned one.
