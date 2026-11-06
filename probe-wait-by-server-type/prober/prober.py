import http.client
import os
import sys
import threading
import time

path = sys.argv[1]
port = int(sys.argv[2])
targets = [a.split("=", 1) for a in sys.argv[3:]]
lock = threading.Lock()


def out(line):
    with lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def probe(pod, ip, t0):
    conn = http.client.HTTPConnection(ip, port, timeout=30)
    result = "ok"
    try:
        conn.request("GET", path, headers={"Connection": "close"})
        r = conn.getresponse()
        r.read()
        result = str(r.status)
    except Exception as e:
        result = type(e).__name__ + ":" + str(e).replace(" ", "_")[:80]
    finally:
        conn.close()
    t1 = time.time()
    out("p %s %.6f %.6f %s\n" % (pod, t0, t1 - t0, result))


nxt = time.time() // 1 + 1
while True:
    time.sleep(max(0, nxt - time.time()))
    for pod, ip in targets:
        threading.Thread(target=probe, args=(pod, ip, time.time()), daemon=True).start()
    nxt += 1
