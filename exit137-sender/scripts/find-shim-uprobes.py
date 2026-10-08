#!/usr/bin/env python3
import re, struct, subprocess, sys

path = sys.argv[1]
data = open(path, "rb").read()
hdr = subprocess.run(["objdump", "-h", path], capture_output=True, text=True, check=True).stdout


def section(name):
    m = re.search(re.escape(name) + r"\s+([0-9a-f]+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)", hdr)
    return [int(x, 16) for x in m.groups()]


size, _, off = section(".gopclntab")
p = data[off:off + size]
assert struct.unpack_from("<I", p, 0)[0] == 0xFFFFFFF1, "unexpected pclntab magic (Go 1.20+ expected)"
nfunc, _, text, fnoff, _, _, _, pcloff = (struct.unpack_from("<Q", p, 8 + i * 8)[0] for i in range(8))
funcs = {}
for i in range(nfunc):
    eoff, foff = struct.unpack_from("<II", p, pcloff + i * 8)
    noff = struct.unpack_from("<i", p, pcloff + foff + 4)[0]
    name = p[fnoff + noff:p.index(b"\0", fnoff + noff)].decode()
    nxt = struct.unpack_from("<I", p, pcloff + (i + 1) * 8)[0]
    funcs[name] = (text + eoff, nxt - eoff)

CG = "github.com/containerd/cgroups/v3/cgroup2."
f1, f1size = funcs[CG + "(*Manager).EventChan.func1"]
callee = {
    funcs[CG + "(*Manager).isCgroupEmpty"][0]: "isCgroupEmpty",
    funcs[CG + "readKVStatsFile"][0]: "readKVStatsFile",
    funcs["runtime.chansend1"][0]: "chansend1",
    funcs["os.Lstat"][0]: "os.Lstat",
    funcs["os.underlyingErrorIs"][0]: "underlyingErrorIs",
}
dis = subprocess.run(["objdump", "-d", f"--start-address={f1:#x}", f"--stop-address={f1 + f1size:#x}", path],
                     capture_output=True, text=True, check=True).stdout
ins = []
for line in dis.splitlines():
    m = re.match(r"\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)", line)
    if m:
        ins.append((int(m.group(1), 16), m.group(2), m.group(3)))


def idx_of_call(name, nth=0):
    hits = [i for i, (a, op, arg) in enumerate(ins) if op == "bl" and callee.get(int(arg.split()[0], 16)) == name]
    return hits[nth]


def nxt(i):
    return ins[i + 1][0]


out = {}
out["isempty"] = nxt(idx_of_call("isCgroupEmpty"))
out["readmev"] = nxt(idx_of_call("readKVStatsFile"))
s = idx_of_call("chansend1", 0)
out["sendev"] = ins[s][0]
t = next(i for i in range(s, len(ins)) if ins[i][1] == "tbz")
out["retev"] = nxt(t)
u = idx_of_call("underlyingErrorIs")
b = next(i for i in range(u, len(ins)) if ins[i][1] == "tbnz")
out["senderr"] = nxt(b)
out["retsilent"] = int(ins[b][2].split(",")[-1].strip().split()[0], 16)
out["readerr"] = ins[idx_of_call("chansend1", 2)][0]
out["oomevent"] = funcs["github.com/containerd/containerd/v2/cmd/containerd-shim-runc-v2/task.(*service).oomEvent"][0]

tsize, tvma, toff = section(".text")
for k, a in out.items():
    print(f"{k} {a:#x} file_offset={a - tvma + toff:#x}")
