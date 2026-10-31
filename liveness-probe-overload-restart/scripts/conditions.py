import os

C = int(os.environ["C"])
OVER = float(os.environ.get("OVER", "1.2"))
OVER_SECONDS = int(os.environ.get("OVER_SECONDS", "60"))
TOTAL = 180
WARM = 15


def overload(back):
    return [(0.75, WARM), (OVER, OVER_SECONDS), (back, TOTAL - WARM - OVER_SECONDS)]


CONDITIONS = {
    "capacity": {"load": "closed", "vus": 60, "seconds": 40},
    "P1": {"load": overload(0.9)},
    "P2": {"load": [(0.75, WARM), (0.9, TOTAL - WARM)]},
    "P3": {"load": overload(0.9), "probe_port": 8081},
    "P4": {"load": overload(0.9), "liveness_timeout": 5},
    "P5": {"load": overload(0.9), "liveness": False},
}


def stages(cond):
    load = CONDITIONS[cond]["load"]
    if not isinstance(load, list):
        return []
    return [(round(f * C), s, f) for f, s in load]
