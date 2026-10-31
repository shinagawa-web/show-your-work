import ctypes
import os
import time

_t = time.time()
_libc = ctypes.CDLL("libc.so.6")
_libc.malloc_trim.restype = ctypes.c_int
_libc.malloc_trim.argtypes = [ctypes.c_size_t]
_t0 = time.perf_counter()
_r = _libc.malloc_trim(0)
_dt = time.perf_counter() - _t0
_fd = os.open("/out/events.txt", os.O_WRONLY | os.O_APPEND)
os.write(_fd, ("injected %.3f %.6f %d\n" % (_t, _dt, _r)).encode())
os.close(_fd)
