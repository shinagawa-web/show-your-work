# imagepullbackoff-events-message

This runs Pods in a kind cluster that fail to pull their image for one cause each, and records the `Failed to pull image` Event the kubelet writes, the `STATUS` that `kubectl get pod` shows, the `state.waiting.message` of the container during back-off, and the `FailedToRetrieveImagePullSecret` Event. The registry is `registry:3` with htpasswd Basic auth, set up as in kind's local registry guide, and public images on Docker Hub, pulled without an account.

## Run

`.github/workflows/imagepullbackoff-events-message.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The `s2-s3` job (named after the article sections that use its numbers) runs `./run-all.sh`, writes `results/summary.md` to the job summary and to a comment on the open pull request of the branch (the same comment is overwritten on every run), and uploads `results/` as the `imagepullbackoff-events-message-results` artifact.

The summary has a table of the first `Failed to pull image` Event message of each condition with the prefixes removed by `scripts/summary.py`, whether the messages of the conditions without credentials are the same once the image name is replaced, and the back-off message of one Pod. A run that did not collect the data (a step of the setup failed, a proxy variable with a value is set in containerd's environment on a node, a baseline Pod did not reach Running, a Pod did not reach the state its condition waits for before the deadline) fails the job with `RUN INVALID` at the top of the summary.

## Setup

`scripts/cluster.sh` creates the registry and the cluster in the order of kind's `kind-with-registry.sh`: the `kind-registry` container on `127.0.0.1:5001`, a `hosts.toml` on each node that maps `localhost:5001` to `http://kind-registry:5000`, the registry connected to the `kind` network, and the `local-registry-hosting` ConfigMap. The registry has `REGISTRY_AUTH=htpasswd` with one user (`scripts/versions.env`). Proxy variables are unset before the cluster is created, and kind reads an empty Docker client configuration.

`scripts/push.sh` copies busybox from Docker Hub into the registry with crane: the whole index as `app:1.0`, an index with only the platform the node does not have as `platform-only:1.0`, and the single manifest of that platform as `single-arch:1.0`. The other platform is `amd64` on an `aarch64` node and `arm64` on an `x86_64` node.

## Conditions

One control-plane node and two workers (`k8s/kind-causes.yaml`). Every Pod is in namespace `cond`, has `imagePullPolicy: Always`, and is placed with `nodeName`: `no-response` on `ipb-worker2`, which has `iptables -I OUTPUT -d 192.0.2.1 -j DROP` and runs no other Pod, and every other Pod on `ipb-worker`. The kubelet pulls one image at a time per node (`serializeImagePulls`), so the wait of `no-response` for the dial timeout does not hold up the other pulls.

The conditions, their images and secrets are in `scripts/conditions.py`. The secrets are created in `scripts/causes.sh`: `regcred` for `localhost:5001`, `regcred-port-5000` for `localhost:5000`, `regcred-bad-password` with a wrong password, `regcred-elsewhere` only in namespace `elsewhere`, and `hub-bad-creds` for `https://index.docker.io/v1/` with a user name and password that do not exist. `no-such-secret` is not created.

The run ends when every Pod has reached the state its condition waits for (`scripts/wait.py`), the Pod named by `INTERVAL_POD` in `scripts/conditions.py` has failed to pull `PULLS` times (`scripts/causes.sh`), and the Pod named by `BACKOFF_POD` has been recorded once in `ImagePullBackOff`; or at `DEADLINE` seconds after the Pods are created.

## Outputs

Under `results/causes/`:

| File | What it records |
|---|---|
| `environment.txt` | kind and Kubernetes versions, the nodes, and for each node: `uname -m`, containerd and kubelet versions, proxy variables in containerd's environment, `/proc/sys/fs/binfmt_misc`, the containerd registry settings and `hosts.toml`, the kubelet configuration (`configz`) |
| `host-proxy.txt` | Proxy variables of the host and of Docker before the cluster is created |
| `images.txt` | The manifest type, platforms and config architecture of each image pushed to the registry |
| `events.jsonl` | Every Event update as received by `kubectl get events --watch` (`scripts/watch.py`) |
| `pods.jsonl` | The `STATUS` column and container state of each Pod each time they change, polled every second (`scripts/poll.py`) |
| `get-pods.txt`, `describe.txt`, `events.txt` | `kubectl get pod`, `kubectl describe pod` and `kubectl get events` at the end |
| `backoff-<pod>.txt` | `kubectl get pod`, the `state.waiting.message` by jsonpath and `kubectl describe pod`, run one after the other while the Pod is in `ImagePullBackOff` |
| `single-arch.txt` | `kubectl logs` and the container state of `single-arch-mismatch` |
| `no-response.txt` | iptables counters and containerd log lines for 192.0.2.1 on `ipb-worker2` |
| `hub-wrong-creds.txt` | containerd log lines for `auth.docker.io`, and the HTTP status of a POST and a GET token request to `auth.docker.io` with the credentials of `hub-bad-creds`, from `ipb-worker` |
| `elapsed.txt` | Seconds for the setup, until the Pods were created, and in total |

`results/elapsed.txt` has the seconds of `scripts/setup.sh` and of the conditions.

## Pinned versions

`scripts/versions.env`.
