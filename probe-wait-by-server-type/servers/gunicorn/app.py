import multiprocessing
import os
import threading
import time

WORK_SECONDS = float(os.environ.get("WORK_MS", "20")) / 1000
BODY = b"ok\n"
sem = multiprocessing.Semaphore(4)
lock = threading.Lock()
counts = {"arrived": 0, "done": 0, "waiting": 0, "inflight": 0}
started = {"pid": None}


def stats():
    prev = time.time()
    while True:
        time.sleep(1 - (time.time() % 1))
        now = time.time()
        with lock:
            a, d, w, i = counts["arrived"], counts["done"], counts["waiting"], counts["inflight"]
            counts["arrived"] = counts["done"] = 0
        os.write(1, ("stat %.3f %.3f %d %d %d %d %d\n" % (prev, now, os.getpid(), a, d, w, i)).encode())
        prev = now


def app(environ, start_response):
    if started["pid"] != os.getpid():
        started["pid"] = os.getpid()
        threading.Thread(target=stats, daemon=True).start()
    if environ.get("PATH_INFO", "") == "/work":
        with lock:
            counts["arrived"] += 1
            counts["waiting"] += 1
            counts["inflight"] += 1
        sem.acquire()
        with lock:
            counts["waiting"] -= 1
        try:
            time.sleep(WORK_SECONDS)
        finally:
            sem.release()
        with lock:
            counts["done"] += 1
            counts["inflight"] -= 1
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(BODY)))])
    return [BODY]
