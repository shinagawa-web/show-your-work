import sys, time

pid, interval, duration, port = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
files = [f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"]
lport = ":%04X" % port


def recvq():
    q = None
    for f in files:
        for l in open(f).readlines()[1:]:
            c = l.split()
            if c[3] == "0A" and c[1].endswith(lport):
                q = (q or 0) + int(c[4].split(":")[1], 16)
    return q


print("t_unix_ns,recv_q", flush=True)
end = time.time() + duration
nxt = time.time()
while time.time() < end:
    print(f"{time.time_ns()},{recvq()}", flush=True)
    nxt += interval
    time.sleep(max(0, nxt - time.time()))
