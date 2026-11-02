import json
import os


def load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def events(path, ns=None):
    out = []
    for r in load(path):
        o = r["obj"]
        if ns and o.get("metadata", {}).get("namespace") != ns:
            continue
        out.append({
            "recv": r["recv"],
            "pod": o.get("involvedObject", {}).get("name"),
            "kind": o.get("involvedObject", {}).get("kind"),
            "type": o.get("type"),
            "reason": o.get("reason"),
            "count": o.get("count") or 1,
            "first": o.get("firstTimestamp") or o.get("eventTime"),
            "last": o.get("lastTimestamp") or o.get("eventTime"),
            "source": (o.get("source") or {}).get("component") or o.get("reportingComponent"),
            "message": o.get("message"),
        })
    return out


def pull_failures(evs, pod):
    return [e for e in evs if e["pod"] == pod and e["reason"] == "Failed" and (e["message"] or "").startswith("Failed to pull image")]


def first_pull_failure(evs, pod):
    f = pull_failures(evs, pod)
    return f[0] if f else None


def secret_events(evs, pod):
    return [e for e in evs if e["pod"] == pod and e["reason"] == "FailedToRetrieveImagePullSecret"]


def pod_states(path, pod):
    return [r for r in load(path) if r["pod"] == pod]


def waiting(r):
    return ((r.get("state") or {}).get("waiting")) or {}


def backoff_record(states):
    for r in states:
        if waiting(r).get("reason") == "ImagePullBackOff":
            return r
    return None


def running(states):
    return any("running" in (r.get("state") or {}) for r in states)


def terminated(states):
    for r in states:
        for k in ("state", "lastState"):
            t = (r.get(k) or {}).get("terminated")
            if t:
                return t
    return None


def statuses_seen(states):
    seen = []
    for r in states:
        s = r.get("status")
        if s and (not seen or seen[-1] != s):
            seen.append(s)
    return seen
