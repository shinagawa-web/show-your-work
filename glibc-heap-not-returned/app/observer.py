import os
import subprocess
import sys
import time

inject = sys.argv[1] == "1"
pre_s, post_s = float(sys.argv[2]), float(sys.argv[3])
work_args = sys.argv[4:]
events = "/out/events.txt"


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def cap_eff():
    for line in open("/proc/self/status"):
        if line.startswith("CapEff:"):
            return line.split()[1]


with open("/out/env.txt", "w") as f:
    f.write("glibc %s\n" % sh("getconf GNU_LIBC_VERSION"))
    f.write("ldd %s\n" % sh("ldd --version | head -1"))
    f.write("uname_m %s\n" % sh("uname -m"))
    f.write("pagesize %s\n" % sh("getconf PAGESIZE"))
    f.write("python %s\n" % sys.version.split()[0])
    f.write("gil_enabled %s\n" % sys._is_gil_enabled())
    f.write("ptrace_scope %s\n" % sh("cat /proc/sys/kernel/yama/ptrace_scope"))
    f.write("cap_eff %s\n" % cap_eff())
    f.write("malloc_env %s\n" % " ".join("%s=%s" % (k, v) for k, v in sorted(os.environ.items()) if k.startswith(("MALLOC_", "GLIBC_TUNABLES"))))
    f.write("ld_preload %s\n" % os.environ.get("LD_PRELOAD", "none"))
    f.write("pythonmalloc %s\n" % os.environ.get("PYTHONMALLOC", "none"))
    try:
        preload = " ".join(open("/etc/ld.so.preload").read().split()) or "empty"
    except FileNotFoundError:
        preload = "none"
    f.write("ld_so_preload %s\n" % preload)

open(events, "w").close()
t0 = time.time()
with open("/out/t0.txt", "w") as f:
    f.write("%.3f\n" % t0)
child = subprocess.Popen([sys.executable, "/app/workload.py", events] + work_args,
                         stdout=open("/out/workload-stdout.txt", "w"), stderr=subprocess.STDOUT)
pid = child.pid


def sample():
    with open("/proc/%d/status" % pid) as f:
        rss = next(int(l.split()[1]) for l in f if l.startswith("VmRSS:"))
    hsize = hrss = None
    with open("/proc/%d/smaps" % pid) as f:
        inheap = False
        for l in f:
            if l[0] in "0123456789abcdef" and "-" in l.split()[0] and not l.split()[0].endswith(":"):
                inheap = l.rstrip().endswith("[heap]")
            elif inheap and l.startswith("Size:"):
                hsize = int(l.split()[1])
            elif inheap and l.startswith("Rss:"):
                hrss = int(l.split()[1])
    hend = None
    with open("/proc/%d/maps" % pid) as f:
        for l in f:
            if l.rstrip().endswith("[heap]"):
                hend = l.split()[0].split("-")[1]
    return rss, hsize, hrss, hend


out = open("/out/samples.tsv", "w")
out.write("t\tvmrss_kib\theap_size_kib\theap_rss_kib\theap_end\n")
idle_at = None
nxt = t0
status = 0
while True:
    if child.poll() is not None:
        status = 1
        break
    try:
        rss, hsize, hrss, hend = sample()
    except (OSError, StopIteration):
        status = 1
        break
    now = time.time()
    out.write("%.3f\t%d\t%s\t%s\t%s\n" % (now, rss, hsize if hsize is not None else "-",
                                         hrss if hrss is not None else "-", ("0x" + hend) if hend else "-"))
    out.flush()
    if idle_at is None and "\nidle " in "\n" + open(events).read():
        idle_at = now
    if idle_at is not None and now - idle_at >= (pre_s + post_s if inject else pre_s):
        break
    nxt += 0.5
    time.sleep(max(0, nxt - time.time()))
child.kill()
child.wait()
out.close()
sys.exit(status)
