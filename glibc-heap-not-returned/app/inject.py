import sys
import time

pid = int(sys.argv[1])
t = time.time()
try:
    sys.remote_exec(pid, "/app/trim.py")
    result = "ok"
except Exception as e:
    result = "error %s %s" % (type(e).__name__, " ".join(str(e).split()))
with open("/out/inject.txt", "w") as f:
    f.write("call %.3f %.3f %s\n" % (t, time.time(), result))
