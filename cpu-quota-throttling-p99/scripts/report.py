import json, os, sys


def load(d):
    return json.load(open(os.path.join(d, "summary.json")))


def get(o, path):
    for k in path.split("."):
        o = o.get(k) if isinstance(o, dict) else None
    return o


def show(d, keys):
    o = load(d)
    print(json.dumps({k: get(o, k) for k in keys}, ensure_ascii=False, indent=1))


COLS = [
    ("host", "host"), ("cpu_model", "cpu_model"),
    ("cpus", "cond.cpus"), ("W", "cond.W"), ("RPS", "cond.rps"), ("mode", "cond.mode"), ("dur_s", "cond.dur"),
    ("util_60s_%", "util_window_pct"), ("util_1s_max_%", "util_1s_pct.max"),
    ("throttle_rate", "throttle_rate"), ("p50_ms", "latency_ms.p50"), ("p99_ms", "latency_ms.p99"), ("max_ms", "latency_ms.max"),
    ("cpus_in_use_mean", "cpus_in_use_10ms.mean"), ("cpus_in_use_max", "cpus_in_use_10ms.max"),
    ("recvq_max", "recvq.max"), ("recvq_mean_first10s", "recvq.mean_first10s"), ("recvq_mean_last10s", "recvq.mean_last10s"),
    ("req_crossing_throttle", "requests_crossing_throttle.n_ge1"), ("n", "requests_crossing_throttle.n"), ("errors", "n_errors"),
]


def table(dirs):
    print("| run | " + " | ".join(c for c, _ in COLS) + " |")
    print("|" + "---|" * (len(COLS) + 1))
    for d in dirs:
        o = load(d)
        print(f"| {os.path.basename(d.rstrip('/'))} | " + " | ".join(str(get(o, p)) for _, p in COLS) + " |")


if __name__ == "__main__":
    if sys.argv[1] == "show":
        show(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "table":
        table(sys.argv[2:])
