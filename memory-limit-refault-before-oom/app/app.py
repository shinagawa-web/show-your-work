import asyncio
import os
import platform
import sys
import time
from contextlib import asynccontextmanager
from importlib import metadata

import numpy as np
from fastapi import FastAPI

CACHE_MB = float(os.environ.get("CACHE_MB", "0"))
ELEM = int(os.environ.get("ELEM_KB", "1024")) * 1024
SIZE = int(os.environ.get("WORK_N", "64"))
EXTRA = [x for x in os.environ.get("EXTRA", "").split(",") if x]

cache = {}
grow_state = {"task": None, "rate": 0.0, "started": None, "per_requests": 0, "served": 0}
rng = np.random.default_rng(0)
base = rng.standard_normal((SIZE, SIZE))
payload = rng.bytes(16384)

if "hashlib" in EXTRA:
    import hashlib
if "zlib" in EXTRA:
    import zlib
if "orjson" in EXTRA:
    import orjson


def fill(target):
    while len(cache) * ELEM < target:
        add()


def add():
    cache[len(cache)] = b"x" * ELEM
    if grow_state["started"] is not None:
        print(f"GROW t={time.time():.6f} elements={len(cache)}", flush=True)


def work():
    a = base + rng.standard_normal(base.shape) * 1e-3
    b = a @ a.T
    w = np.linalg.eigvalsh(b)
    f = np.fft.rfft(a, axis=1)
    s = np.sort(a, axis=None)
    v = float(w[-1] + np.abs(f).sum() + s[s.size // 2])
    if "hashlib" in EXTRA:
        v += hashlib.sha256(payload).digest()[0] + hashlib.blake2b(payload).digest()[0]
    if "zlib" in EXTRA:
        v += len(zlib.compress(payload, 6))
    if "orjson" in EXTRA:
        v += len(orjson.dumps({"w": w.tolist(), "s": s[:64].tolist()}))
    return v


async def grow(rate):
    t0 = time.monotonic()
    start = len(cache) * ELEM
    while True:
        fill(start + rate * (time.monotonic() - t0))
        await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(app):
    fill(CACHE_MB * 2**20)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    v = work()
    n = grow_state["per_requests"]
    if n:
        grow_state["served"] += 1
        if grow_state["served"] % n == 0:
            add()
    return {"v": v}


@app.post("/grow")
async def start_grow(kbps: float = 0, per_requests: int = 0):
    if grow_state["started"] is None:
        grow_state["started"] = time.time()
        if per_requests > 0:
            grow_state["per_requests"] = per_requests
        else:
            grow_state["rate"] = kbps * 1024
            grow_state["task"] = asyncio.create_task(grow(kbps * 1024))
    return {"rate_bytes_per_s": grow_state["rate"], "per_requests": grow_state["per_requests"], "elem_bytes": ELEM, "elements": len(cache), "started": grow_state["started"]}


@app.get("/info")
async def info():
    loop = asyncio.get_running_loop()
    http = sorted(m for m in sys.modules if m.startswith("uvicorn.protocols.http."))
    pkgs = {p: metadata.version(p) for p in ("fastapi", "uvicorn", "uvloop", "httptools", "numpy", "starlette", "pydantic", "pydantic-core", "orjson") if _has(p)}
    return {
        "python": platform.python_version(),
        "packages": pkgs,
        "loop": f"{type(loop).__module__}.{type(loop).__name__}",
        "http_modules": http,
        "numpy_config": _blas(),
        "cache_elements": len(cache),
        "elem_bytes": ELEM,
        "cache_bytes": len(cache) * ELEM,
        "work_n": SIZE,
        "extra": EXTRA,
        "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
    }


def _has(p):
    try:
        metadata.version(p)
        return True
    except metadata.PackageNotFoundError:
        return False


def _blas():
    try:
        cfg = np.show_config(mode="dicts")
        return cfg.get("Build Dependencies", {}).get("blas", {})
    except Exception as e:
        return str(e)
