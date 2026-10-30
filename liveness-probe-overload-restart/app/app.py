import os
import time

WORK_SECONDS = float(os.environ.get("WORK_SECONDS", "0.02"))
BODY = b"ok\n"


def app(environ, start_response):
    t0 = time.time()
    path = environ.get("PATH_INFO", "")
    if path != "/healthz":
        time.sleep(WORK_SECONDS)
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(BODY)))])
    t1 = time.time()
    line = "req %.6f %.6f %s %s %s:%s %d\n" % (
        t0, t1, path, environ.get("SERVER_PORT"), environ.get("REMOTE_ADDR"), environ.get("REMOTE_PORT"), os.getpid())
    os.write(1, line.encode())
    return [BODY]
