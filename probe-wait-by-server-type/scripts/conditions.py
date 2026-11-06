import os

C = 600
PODS = 3
WARM = 15
OVER_SECONDS = 60
BACK_SECONDS = 105
STAGES = [(0.75, WARM), (1.2, OVER_SECONDS), (0.75, BACK_SECONDS)]


def env_key(server):
    return server.replace("-", "_")


def work_ms(server):
    return os.environ.get("WORK_MS_" + env_key(server), "20")


def capacity(server):
    return float(os.environ["RPS_" + env_key(server)])


def stages(mode):
    if mode == "capacity":
        return []
    return [(round(f * C), s, f) for f, s in STAGES]
