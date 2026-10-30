import ctypes
import gc
import os
import resource
import sys
import time

out, keep_size, trim_after, cycles, peak_n, obj_size, hold_s, wait_s = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1", int(sys.argv[4]),
    int(sys.argv[5]), int(sys.argv[6]), float(sys.argv[7]), float(sys.argv[8]))


class MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd",
        "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


libc = ctypes.CDLL("libc.so.6")
libc.mallinfo2.restype = MallInfo2
libc.mallinfo2.argtypes = []
libc.malloc_trim.restype = ctypes.c_int
libc.malloc_trim.argtypes = [ctypes.c_size_t]
libc.mallinfo2()
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
buf = bytearray(4096)
kept = [None] * (cycles + 1)


def emit(s):
    os.write(fd, s.encode())


def rss():
    f = os.open("/proc/self/status", os.O_RDONLY)
    os.readv(f, [buf])
    os.close(f)
    i = buf.find(b"VmRSS:")
    j = buf.find(b"\n", i)
    return int(buf[i + 6:j].split()[0])


def mi(cycle, at):
    m = libc.mallinfo2()
    emit("mi %.3f %d %s %d %d %d %d\n" % (time.time(), cycle, at, m.arena, m.uordblks, m.fordblks, m.hblkhd))


emit("start %.3f %d\n" % (time.time(), os.getpid()))
rss()
gc.collect()
resource.getrusage(resource.RUSAGE_SELF).ru_minflt
mi(0, "prep")
time.perf_counter()
for c in range(1, cycles + 1):
    peak = [None] * peak_n
    f0 = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    t0 = time.perf_counter()
    for i in range(peak_n):
        peak[i] = b"\x01" * obj_size
    dt = time.perf_counter() - t0
    f1 = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    k = 0
    if keep_size:
        kept[c] = b"\x02" * keep_size
        k = id(kept[c])
    emit("peak %.3f %d %.6f %d %d %d %d\n" % (time.time(), c, dt, id(peak[0]), id(peak[-1]), k, f1 - f0))
    time.sleep(hold_s)
    mi(c, "hold")
    peak = None
    a = rss()
    gc.collect()
    b = rss()
    emit("rel %.3f %d %d %d\n" % (time.time(), c, a, b))
    if trim_after:
        t0 = time.perf_counter()
        r = libc.malloc_trim(0)
        dt = time.perf_counter() - t0
        emit("trim %.3f %d %.6f %d\n" % (time.time(), c, dt, r))
    time.sleep(wait_s)
    mi(c, "wait")
emit("idle %.3f\n" % time.time())
while True:
    time.sleep(0.05)
