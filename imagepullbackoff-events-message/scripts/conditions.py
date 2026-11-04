import json
import sys

LOCAL = "localhost:5001"
HUB = "docker.io/library/busybox:1.37.0"

CAUSES = [
    dict(name="local-baseline", base="local", expect="run", image=f"{LOCAL}/app:1.0", secrets=["regcred"],
         what="Correct name, tag and secret, pulled from the local registry"),
    dict(name="hub-baseline", base="hub", expect="run", image=HUB, secrets=[],
         what="Public Docker Hub image, no credentials"),
    dict(name="tag-missing", base="local", expect="fail", image=f"{LOCAL}/app:no-such-tag", secrets=["regcred"],
         what="Tag that does not exist (correct secret)"),
    dict(name="repo-missing", base="local", expect="fail", image=f"{LOCAL}/no-such-repo:1.0", secrets=["regcred"],
         what="Repository that does not exist (correct secret)"),
    dict(name="no-creds-repo-exists", base="local", expect="fail", image=f"{LOCAL}/app:1.0", secrets=[],
         what="No imagePullSecrets, repository exists"),
    dict(name="no-creds-repo-missing", base="local", expect="fail", image=f"{LOCAL}/no-such-repo:1.0", secrets=[],
         what="No imagePullSecrets, repository does not exist"),
    dict(name="secret-wrong-host", base="local", expect="fail", image=f"{LOCAL}/app:1.0", secrets=["regcred-port-5000"],
         what="Secret created with --docker-server=localhost:5000, image on localhost:5001"),
    dict(name="secret-other-namespace", base="local", expect="fail", image=f"{LOCAL}/app:1.0", secrets=["regcred-elsewhere"],
         what="imagePullSecrets names a secret that exists only in another namespace"),
    dict(name="secret-name-typo", base="local", expect="fail", image=f"{LOCAL}/app:1.0", secrets=["regcred-typo"],
         what="imagePullSecrets names a secret that exists in no namespace"),
    dict(name="wrong-password", base="local", expect="fail", image=f"{LOCAL}/app:1.0", secrets=["regcred-bad-password"],
         what="Secret with the wrong password (Basic auth)"),
    dict(name="host-unresolvable", base="local", expect="fail", image="registry.invalid:5000/app:1.0", secrets=["regcred"],
         what="Registry host name under .invalid"),
    dict(name="port-closed", base="local", expect="fail", image="kind-registry:5999/app:1.0", secrets=["regcred"],
         what="Reachable host (the registry container), closed port 5999"),
    dict(name="no-response", base="local", expect="fail", image="192.0.2.1:5000/app:1.0", secrets=["regcred"], node="worker2",
         what="192.0.2.1 with iptables -I OUTPUT -d 192.0.2.1 -j DROP on the node (own node)"),
    dict(name="platform-missing", base="local", expect="fail", image=f"{LOCAL}/platform-only:1.0", secrets=["regcred"],
         what="Image index that has only a platform other than the node's"),
    dict(name="single-arch-mismatch", base="local", expect="crash", image=f"{LOCAL}/single-arch:1.0", secrets=["regcred"],
         what="Single manifest (no index) for an architecture other than the node's"),
    dict(name="hub-no-creds-repo-missing", base="hub", expect="fail", image="docker.io/library/imagepullbackoff-no-such-repo:1.0", secrets=[],
         what="Docker Hub repository that does not exist, no credentials"),
    dict(name="hub-wrong-creds", base="hub", expect="fail", image=HUB, secrets=["hub-bad-creds"],
         what="Public Docker Hub image with a secret whose user name does not fit the Docker ID format"),
    dict(name="hub-wrong-creds-valid-name", base="hub", expect="fail", image=HUB, secrets=["hub-bad-creds-valid-name"],
         what="Public Docker Hub image with a secret whose user name fits the Docker ID format and does not exist"),
    dict(name="hub-missing-secret-name", base="hub", expect="run", image=HUB, secrets=["no-such-secret"],
         what="Public Docker Hub image, imagePullSecrets names a secret that does not exist"),
    dict(name="hub-missing-secret-name-pull-fails", base="hub", expect="fail", image="docker.io/library/imagepullbackoff-no-such-repo:1.0", secrets=["no-such-secret"],
         what="Docker Hub repository that does not exist, imagePullSecrets names a secret that does not exist"),
]

BACKOFF_POD = "tag-missing"
INTERVAL_POD = "tag-missing"


def pod(name, image, secrets, node, policy, ns):
    spec = {
        "nodeName": node,
        "terminationGracePeriodSeconds": 0,
        "containers": [{"name": "main", "image": image, "imagePullPolicy": policy, "command": ["sleep", "3600"]}],
    }
    if secrets:
        spec["imagePullSecrets"] = [{"name": s} for s in secrets]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": ns}, "spec": spec}


def main():
    what = sys.argv[1]
    if what == "causes":
        cluster, ns = sys.argv[2], sys.argv[3]
        items = [pod(c["name"], c["image"], c["secrets"], f"{cluster}-{c.get('node', 'worker')}", "Always", ns) for c in CAUSES]
        print(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))
    elif what == "names":
        print("\n".join(c["name"] for c in CAUSES))


if __name__ == "__main__":
    main()
