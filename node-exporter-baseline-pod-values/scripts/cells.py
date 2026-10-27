import json
import os
import re
import socket
import subprocess
import sys
import urllib.parse
import urllib.request

LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)')
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
UDEV_KEYS = ("path", "wwn", "model", "serial", "revision")
K3S_RUN = re.compile(r"^/run/k3s/containerd/")


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def kjson(*args):
    return json.loads(sh("kubectl", *args, "-o", "json"))


def read(path):
    with open(path) as f:
        return f.read()


def parse_metrics(text):
    out = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = LINE.match(line)
        if m:
            out.append((m.group(1), dict(LABEL.findall(m.group(2) or "")), float(m.group(3))))
    return out


def get(ms, name, **want):
    return [(l, v) for n, l, v in ms if n == name and all(l.get(k) == x for k, x in want.items())]


def one(ms, name, **want):
    rows = get(ms, name, **want)
    return rows[0][1] if rows else None


def labelset(ms, name, key):
    return sorted({l.get(key, "") for l, _ in get(ms, name)})


def netdev(pid):
    out = {}
    for line in read(f"/proc/{pid}/net/dev").splitlines()[2:]:
        name, rest = line.split(":", 1)
        out[name.strip()] = int(rest.split()[0])
    return out


def snmp_tcp(pid):
    lines = [l.split() for l in read(f"/proc/{pid}/net/snmp").splitlines() if l.startswith("Tcp:")]
    return dict(zip(lines[0][1:], (int(x) for x in lines[1][1:])))


def sockstat(pid):
    out = {}
    for line in read(f"/proc/{pid}/net/sockstat").splitlines():
        proto, rest = line.split(":", 1)
        f = rest.split()
        for k, v in zip(f[0::2], f[1::2]):
            out["sockets_used" if proto == "sockets" else f"{proto}_{k}"] = int(v)
    return out


def conntrack_count(pid):
    if pid == 1:
        return int(read("/proc/sys/net/netfilter/nf_conntrack_count"))
    return int(sh("nsenter", "-t", str(pid), "-n", "cat", "/proc/sys/net/netfilter/nf_conntrack_count"))


def cpu0_user_ticks():
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu0 "):
            return int(line.split()[1])


def disk_read_sectors(dev):
    for line in read("/proc/diskstats").splitlines():
        f = line.split()
        if f[2] == dev:
            return int(f[5])


def refs(pid, dev):
    return {
        "netdev": netdev(pid),
        "tcp": snmp_tcp(pid),
        "sockstat": sockstat(pid),
        "conntrack": conntrack_count(pid),
        "cpu0_user_ticks": cpu0_user_ticks(),
        "disk_read_sectors": disk_read_sectors(dev),
    }


def mounts(pid, mp_ex, fs_ex):
    out = []
    for line in read(f"/proc/{pid}/mountinfo").splitlines():
        left, right = line.split(" - ", 1)
        mp = left.split()[4].replace("\\040", " ")
        fs = right.split()[0]
        if mp_ex.search(mp) or fs_ex.search(fs):
            continue
        out.append(mp)
    return sorted(set(out))


def statfs_size(path):
    try:
        s = os.statvfs(path)
    except OSError as e:
        return f"error({e.strerror})"
    return s.f_blocks * s.f_frsize


def mac_in_netns(pid, dev):
    out = sh("nsenter", "-t", str(pid), "-n", "ip", "-o", "link", "show", dev)
    return out.split("link/ether")[1].split()[0]


def bracket(v, a, b, label):
    lo, hi = min(a, b), max(a, b)
    return f"{label}[{a}..{b}]" + (" contains" if lo <= v <= hi else " misses")


def side(v, node_pair, pod_pair, shared=False):
    n = list(node_pair)
    p = list(pod_pair)
    if shared:
        return "node"
    dn = 0 if min(n) <= v <= max(n) else min(abs(v - x) for x in n)
    dp = 0 if min(p) <= v <= max(p) else min(abs(v - x) for x in p)
    return "node" if dn < dp else "pod" if dp < dn else "node=pod"


def combine(vs):
    s = sorted(set(vs))
    return s[0] if len(s) == 1 else "mixed(" + ",".join(vs) + ")"


def pod_info(cond):
    ns = f"ne-{cond}"
    pod = kjson("get", "pod", "-n", ns, "-l", "app.kubernetes.io/name=prometheus-node-exporter")["items"][0]
    cid = sh("crictl", "ps", "--label", f"io.kubernetes.pod.namespace={ns}", "--name", "^node-exporter$", "-q").split()[0]
    pid = json.loads(sh("crictl", "inspect", cid))["info"]["pid"]
    return {
        "ns": ns,
        "name": pod["metadata"]["name"],
        "ip": pod["status"]["podIP"],
        "host_ip": pod["status"]["hostIP"],
        "port": pod["spec"]["containers"][0]["ports"][0]["containerPort"],
        "host_network": pod["spec"].get("hostNetwork", False),
        "pid": pid,
    }


def chart_excludes():
    args = kjson("get", "ds", "prometheus-node-exporter", "-n", "ne-base")["spec"]["template"]["spec"]["containers"][0]["args"]
    mp = next(a.split("=", 1)[1] for a in args if a.startswith("--collector.filesystem.mount-points-exclude="))
    fs = next(a.split("=", 1)[1] for a in args if a.startswith("--collector.filesystem.fs-types-exclude="))
    return re.compile(mp), re.compile(fs)


def cmd_cells(outdir, *conds):
    mp_ex, fs_ex = chart_excludes()
    dev = os.path.basename(sh("findmnt", "-no", "SOURCE", "/").strip())
    dev = sh("lsblk", "-no", "PKNAME", f"/dev/{dev}").strip() or dev
    hostname = socket.gethostname()
    host_ifaces = sorted(netdev(1))
    host_sysnet = sorted(os.listdir("/sys/class/net"))
    host_mac = read("/sys/class/net/eth0/address").strip()
    node_mounts = [m for m in mounts(1, mp_ex, fs_ex) if not K3S_RUN.search(m)]
    root_size = statfs_size("/")
    memtotal = int(re.search(r"^MemTotal:\s+(\d+)", read("/proc/meminfo"), re.M).group(1)) * 1024
    btime = int(re.search(r"^btime (\d+)", read("/proc/stat"), re.M).group(1))
    ct_max = int(read("/proc/sys/net/netfilter/nf_conntrack_max"))
    print(f"node: hostname={hostname} root_disk={dev}")
    print(f"node interfaces (/proc/1/net/dev): {','.join(host_ifaces)}")
    print(f"node /sys/class/net: {','.join(host_sysnet)}")
    print(f"node eth0 address: {host_mac}")
    print(f"node MemTotal bytes: {memtotal}  btime: {btime}  nf_conntrack_max: {ct_max}")
    print(f"node mountpoints (/proc/1/mountinfo, chart excludes applied, {K3S_RUN.pattern} set aside): {','.join(node_mounts)}")
    print(f"node / statfs size: {root_size}")
    print()
    table = {}
    fs_seen = {}
    base = None
    for cond in conds:
        p = pod_info(cond)
        pid = p["pid"]
        n0, p0 = refs(1, dev), refs(pid, dev)
        url = f"http://{p['ip']}:{p['port']}/metrics"
        text = urllib.request.urlopen(url, timeout=10).read().decode()
        n1, p1 = refs(1, dev), refs(pid, dev)
        with open(os.path.join(outdir, f"{cond}.prom"), "w") as f:
            f.write(text)
        ms = parse_metrics(text)
        if base is None:
            base = ms
        pod_ifaces = sorted(p0["netdev"])
        shared = os.readlink(f"/proc/{pid}/ns/net") == os.readlink("/proc/1/ns/net")
        pod_mac = mac_in_netns(pid, "eth0")
        cont_mounts = mounts(pid, mp_ex, fs_ex)
        row = {}
        print(f"=== {cond} ({p['ns']}) pod={p['name']} podIP={p['ip']} hostIP={p['host_ip']} hostNetwork={p['host_network']} pid={pid} scrape={url}")
        print(f"pod netns interfaces (/proc/{pid}/net/dev): {','.join(pod_ifaces)}  eth0 address: {pod_mac}  shares node netns: {shared}")

        cpu = one(ms, "node_cpu_seconds_total", cpu="0", mode="user")
        mem = one(ms, "node_memory_MemTotal_bytes")
        dr = one(ms, "node_disk_read_bytes_total", device=dev)
        bt = one(ms, "node_boot_time_seconds")
        print("[cpu-mem-disk]")
        print(f"  node_cpu_seconds_total{{cpu=\"0\",mode=\"user\"}}={cpu}  /proc/stat cpu0 user ticks/100 {bracket(cpu, n0['cpu0_user_ticks'] / 100, n1['cpu0_user_ticks'] / 100, 'node')}")
        print(f"  node_memory_MemTotal_bytes={mem:.0f}  node /proc/meminfo={memtotal}")
        print(f"  node_disk_read_bytes_total{{device=\"{dev}\"}}={dr:.0f}  /proc/diskstats sectors*512 {bracket(dr, n0['disk_read_sectors'] * 512, n1['disk_read_sectors'] * 512, 'node')}")
        print(f"  node_boot_time_seconds={bt:.0f}  node btime={btime}")
        ok = (n0["cpu0_user_ticks"] / 100 - 0.005 <= cpu <= n1["cpu0_user_ticks"] / 100 + 0.005) and int(mem) == memtotal \
            and n0["disk_read_sectors"] * 512 <= dr <= n1["disk_read_sectors"] * 512 and int(bt) == btime
        row["cpu-mem-disk"] = "node" if ok else "differs"

        nd = labelset(ms, "node_network_receive_bytes_total", "device")
        rx = one(ms, "node_network_receive_bytes_total", device="eth0")
        print("[netdev]")
        print(f"  node_network_receive_bytes_total device set: {','.join(nd)}")
        print(f"  node_network_receive_bytes_total{{device=\"eth0\"}}={rx:.0f}  {bracket(rx, n0['netdev']['eth0'], n1['netdev']['eth0'], 'node eth0')}  {bracket(rx, p0['netdev']['eth0'], p1['netdev']['eth0'], 'pod eth0')}")
        set_v = "node" if nd == host_ifaces else "pod" if nd == pod_ifaces else "other"
        val_v = side(rx, (n0["netdev"]["eth0"], n1["netdev"]["eth0"]), (p0["netdev"]["eth0"], p1["netdev"]["eth0"]), shared)
        print(f"  device set matches: {set_v}  eth0 value matches: {val_v}")
        row["netdev"] = combine([set_v, val_v])

        print("[netstat-sockstat-per-netns]")
        vs = []
        for metric, key, src in (("node_netstat_Tcp_CurrEstab", "CurrEstab", "tcp"),
                                 ("node_sockstat_TCP_inuse", "TCP_inuse", "sockstat"),
                                 ("node_sockstat_sockets_used", "sockets_used", "sockstat"),
                                 ("node_sockstat_TCP_tw", "TCP_tw", "sockstat"),
                                 ("node_sockstat_UDP_inuse", "UDP_inuse", "sockstat")):
            v = one(ms, metric)
            r = side(v, (n0[src][key], n1[src][key]), (p0[src][key], p1[src][key]), shared)
            vs.append(r)
            print(f"  {metric}={v:.0f}  node=[{n0[src][key]}..{n1[src][key]}] pod=[{p0[src][key]}..{p1[src][key]}] -> {r}")
        row["netstat-sockstat"] = combine(vs)

        print("[sockstat-global]")
        vs = []
        for metric, key in (("node_sockstat_TCP_alloc", "TCP_alloc"), ("node_sockstat_UDP_mem", "UDP_mem"),
                            ("node_sockstat_TCP_orphan", "TCP_orphan")):
            v = one(ms, metric)
            nn = (n0["sockstat"][key], n1["sockstat"][key])
            pp = (p0["sockstat"][key], p1["sockstat"][key])
            d = 0 if min(nn) <= v <= max(nn) else min(abs(v - x) for x in nn)
            r = "node" if d <= max(1, 0.01 * max(nn)) else "differs"
            vs.append(r)
            print(f"  {metric}={v:.0f}  node=[{nn[0]}..{nn[1]}] read-in-pod-netns=[{pp[0]}..{pp[1]}] -> {r}")
        row["sockstat-global"] = combine(vs)

        ce = one(ms, "node_nf_conntrack_entries")
        cl = one(ms, "node_nf_conntrack_entries_limit")
        r = side(ce, (n0["conntrack"], n1["conntrack"]), (p0["conntrack"], p1["conntrack"]), shared)
        print("[conntrack]")
        print(f"  node_nf_conntrack_entries={ce:.0f}  node nf_conntrack_count=[{n0['conntrack']}..{n1['conntrack']}] pod netns=[{p0['conntrack']}..{p1['conntrack']}] -> {r}")
        pod_max = int(sh("nsenter", "-t", str(pid), "-n", "cat", "/proc/sys/net/netfilter/nf_conntrack_max"))
        rl = "node" if int(cl) == ct_max else "differs"
        print(f"  node_nf_conntrack_entries_limit={cl:.0f}  node nf_conntrack_max={ct_max} read-in-pod-netns={pod_max} -> {rl}")
        row["conntrack-entries"] = r
        row["conntrack-limit"] = rl

        nc = labelset(ms, "node_network_info", "device")
        addr = [l.get("address") for l, _ in get(ms, "node_network_info", device="eth0")]
        set_v = "node" if nc == host_sysnet else "pod" if nc == pod_ifaces else "other"
        mac_v = "node" if addr == [host_mac] else "pod" if addr == [pod_mac] else "other"
        print("[netclass]")
        print(f"  node_network_info device set: {','.join(nc)}")
        print(f"  node_network_info{{device=\"eth0\"}} address: {addr}  -> set {set_v}, eth0 address {mac_v}")
        row["netclass"] = combine([set_v, mac_v])

        all_errs = {l["mountpoint"]: v for l, v in get(ms, "node_filesystem_device_error")}
        k3s = sorted(m for m in all_errs if K3S_RUN.search(m))
        errs = {m: v for m, v in all_errs.items() if not K3S_RUN.search(m)}
        sizes = {l["mountpoint"]: v for l, v in get(ms, "node_filesystem_size_bytes")}
        devs = {l["mountpoint"]: l.get("device", "") for l, v in get(ms, "node_filesystem_device_error")}
        listed = sorted(errs)
        print("[filesystem]")
        print(f"  {K3S_RUN.pattern} mountpoints set aside: {len(k3s)}, device_error=1 on {sum(1 for m in k3s if all_errs[m] == 1)}")
        cont_mounts = [m for m in cont_mounts if not m.startswith("/host/")]
        print(f"  container mountpoints (/proc/{pid}/mountinfo, chart excludes applied, /host/ hostPath mounts set aside): {','.join(cont_mounts)}")
        print(f"  mountpoints in node_filesystem_device_error: {','.join(listed)}")
        print(f"  device_error=1: {','.join(m for m in listed if errs[m] == 1) or '-'}")
        size_ok = True
        for m in listed:
            node_sz = statfs_size(m)
            cont_sz = statfs_size(f"/proc/{pid}/root{m}")
            sz = sizes.get(m)
            if sz is not None and node_sz != int(sz):
                size_ok = False
            print(f"    {m}: device={devs[m]} device_error={errs[m]:.0f} size_bytes={'-' if sz is None else f'{sz:.0f}'} node statfs={node_sz} statfs in container view={cont_sz}")
        bad = any(errs[m] == 1 for m in listed)
        if listed == node_mounts and not bad:
            fv = "node"
        elif listed == node_mounts and bad:
            fv = "partial-error"
        elif listed == cont_mounts and "/" not in listed:
            fv = "container-list"
        else:
            fv = "other"
        same_root = all(int(sizes[m]) == root_size for m in listed if m in sizes)
        fs_seen[cond] = (fv, listed, [m for m in listed if errs[m] == 1], [m for m in listed if m not in sizes], cont_mounts)
        print(f"  list matches: {fv}  sizes equal node statfs at the same path: {size_ok}  sizes all equal node / size: {same_root}")
        row["filesystem"] = fv

        di = get(ms, "node_disk_info", device=dev)
        bdi = get(base, "node_disk_info", device=dev)
        udev = {k: di[0][0].get(k, "") for k in UDEV_KEYS} if di else {}
        budev = {k: bdi[0][0].get(k, "") for k in UDEV_KEYS} if bdi else {}
        bfams = {n for n, _, _ in base if n.startswith("node_disk_") and n.endswith("_info")}
        fams = {n for n, _, _ in ms if n.startswith("node_disk_") and n.endswith("_info")}
        print("[udev]")
        print(f"  node_disk_info{{device=\"{dev}\"}} udev labels: {udev}")
        print(f"  node_disk_*_info families absent vs base: {','.join(sorted(bfams - fams)) or '-'}")
        if not di:
            uv = "absent"
        elif not any(udev.values()) and any(budev.values()):
            uv = "labels-empty"
        elif udev == budev and fams == bfams:
            uv = "node"
        else:
            uv = "differs"
        row["udev"] = uv

        un = [l.get("nodename") for l, _ in get(ms, "node_uname_info")]
        nv = "node-name" if un == [hostname] else "pod-name" if un == [p["name"]] else "other"
        print("[nodename]")
        print(f"  node_uname_info nodename={un}  node hostname={hostname} pod name={p['name']} -> {nv}")
        row["nodename"] = nv

        print("[invariants vs base]")
        for label, a, b in (("MemTotal", one(base, "node_memory_MemTotal_bytes"), mem),
                            ("boot_time", one(base, "node_boot_time_seconds"), bt),
                            ("cpu set", labelset(base, "node_cpu_seconds_total", "cpu"), labelset(ms, "node_cpu_seconds_total", "cpu")),
                            ("disk device set", labelset(base, "node_disk_read_bytes_total", "device"), labelset(ms, "node_disk_read_bytes_total", "device"))):
            print(f"  {label}: {'equal' if a == b else 'differs'} (base={a} {cond}={b})")
        print()
        table[cond] = row
    rows = ["cpu-mem-disk", "netdev", "netstat-sockstat", "sockstat-global", "conntrack-entries", "conntrack-limit", "netclass", "filesystem", "udev", "nodename"]
    print("| row | " + " | ".join(conds) + " |")
    print("|---|" + "---|" * len(conds))
    for r in rows:
        print(f"| {r} | " + " | ".join(table[c][r] for c in conds) + " |")
    print()
    checked = {
        "cpu-mem-disk": f'`node_cpu_seconds_total{{cpu="0",mode="user"}}`, `node_memory_MemTotal_bytes`, `node_disk_read_bytes_total{{device="{dev}"}}`, `node_boot_time_seconds`',
        "netdev": 'the set of label `device` on `node_network_receive_bytes_total`, and `node_network_receive_bytes_total{device="eth0"}`',
        "netstat-sockstat": "`node_netstat_Tcp_CurrEstab`, `node_sockstat_TCP_inuse`, `node_sockstat_sockets_used`, `node_sockstat_TCP_tw`, `node_sockstat_UDP_inuse`",
        "sockstat-global": "`node_sockstat_TCP_alloc`, `node_sockstat_UDP_mem`, `node_sockstat_TCP_orphan`",
        "conntrack-entries": "`node_nf_conntrack_entries`",
        "conntrack-limit": "`node_nf_conntrack_entries_limit`",
        "netclass": 'the set of label `device` on `node_network_info`, and label `address` on `node_network_info{device="eth0"}`',
        "filesystem": f"the set of label `mountpoint` on `node_filesystem_device_error` (`{K3S_RUN.pattern}` set aside), and the value of `node_filesystem_device_error`",
        "udev": f'labels `{"`, `".join(UDEV_KEYS)}` on `node_disk_info{{device="{dev}"}}`, and the set of `node_disk_*_info` series names, against base',
        "nodename": "label `nodename` on `node_uname_info`",
    }
    print("| series group | checked with |")
    print("|---|---|")
    for r in rows:
        print(f"| {r} | {checked[r]} |")
    print()
    meaning = {
        "node": "matched what the run read on the node itself (the node's /proc, /sys and network namespace); for sockstat-global within 1% or 1; for filesystem, the node's mountpoints with node_filesystem_device_error 0 on all; for udev, the same labels and series names as base",
        "pod": "matched what the run read inside the Pod's own network namespace, and not the node's",
        "node=pod": "as close to the node's value as to the Pod's",
        "differs": "not within the check's tolerance of the node's value, or not the same as base",
        "other": "matched neither the node's nor the Pod's",
        "mixed": "the series of the group disagreed; the result of each is listed in order",
        "partial-error": "the node's mountpoints, but node_filesystem_device_error is 1 on some of them",
        "container-list": "not the node's mountpoints but the container's own mount table (/proc/<pid>/mountinfo with the chart's excludes), without /",
        "labels-empty": f"node_disk_info{{device=\"{dev}\"}} is reported, but its labels {', '.join(UDEV_KEYS)} are all empty, while base has them",
        "absent": f"no node_disk_info{{device=\"{dev}\"}} series",
        "node-name": "label nodename equals the node's hostname",
        "pod-name": "label nodename equals the Pod's name",
    }
    print("| value | meaning |")
    print("|---|---|")
    for k, v in meaning.items():
        print(f"| {k} | {v} |")
    print()
    print("| condition | filesystem | mountpoints | device_error 1 | no size_bytes | container mountpoints |")
    print("|---|---|---|---|---|---|")
    for c in conds:
        fv, listed, err1, nosize, cont = fs_seen[c]
        print(f"| {c} | {fv} | " + " | ".join(",".join(x) or "-" for x in (listed, err1, nosize, cont)) + " |")


def prom(query):
    q = urllib.parse.quote(query, safe="")
    return json.loads(sh("kubectl", "get", "--raw", f"/api/v1/namespaces/prom/services/prometheus:9090/proxy/api/v1/query?query={q}"))["data"]["result"]


def cmd_query(query):
    print(f"query: {query}")
    res = prom(query)
    for s in sorted(res, key=lambda s: s["metric"].get("namespace", "")):
        print(f"  {json.dumps(s['metric'], sort_keys=True)} {s['value'][1]}")
    if not res:
        print("  (empty result)")


def cmd_alert(*conds):
    expr = '(node_nf_conntrack_entries{job="node-exporter"} / node_nf_conntrack_entries_limit) > 0.75'
    queries = {
        "entries": 'node_nf_conntrack_entries{job="node-exporter"}',
        "limit": 'node_nf_conntrack_entries_limit{job="node-exporter"}',
        "ratio": 'node_nf_conntrack_entries{job="node-exporter"} / node_nf_conntrack_entries_limit',
        "expression": expr,
    }
    got = {}
    for key, q in queries.items():
        cmd_query(q)
        got[key] = {s["metric"].get("namespace"): s["value"][1] for s in prom(q)}
    print()
    print("| condition | entries | limit | ratio | expression |")
    print("|---|---|---|---|---|")
    for c in conds:
        ns = f"ne-{c}"
        e = got["expression"].get(ns)
        print(f"| {c} | {got['entries'].get(ns, '-')} | {got['limit'].get(ns, '-')} | {got['ratio'].get(ns, '-')} | {'returned ' + e if e else 'empty'} |")


def cmd_labels(*conds):
    node = kjson("get", "nodes")["items"][0]
    node_ip = next(a["address"] for a in node["status"]["addresses"] if a["type"] == "InternalIP")
    hostname = socket.gethostname()
    print(f"node InternalIP={node_ip} hostname={hostname}")
    cmd_query('node_uname_info{job="node-exporter"}')
    res = {s["metric"]["namespace"]: s["metric"] for s in prom('node_uname_info{job="node-exporter"}')}
    print()
    print("| condition | pod IP | instance | instance is | nodename | nodename is |")
    print("|---|---|---|---|---|---|")
    for c in conds:
        p = pod_info(c)
        m = res.get(f"ne-{c}", {})
        inst = m.get("instance", "-")
        host = inst.rsplit(":", 1)[0]
        iv = "node-ip" if host == node_ip else "pod-ip" if host == p["ip"] else "other"
        nn = m.get("nodename", "-")
        nv = "node-name" if nn == hostname else "pod-name" if nn == p["name"] else "other"
        print(f"| {c} | {p['ip']} | {inst} | {iv} | {nn} | {nv} |")
    print()
    print("| series group | checked with |")
    print("|---|---|")
    print('| instance | label `instance` that Prometheus puts on every series, read on `node_uname_info{job="node-exporter"}` |')
    print()
    print("| value | meaning |")
    print("|---|---|")
    print("| node-ip | the host part of label instance equals the node's InternalIP |")
    print("| pod-ip | the host part of label instance equals the Pod's IP |")
    print("| other | matched neither |")


def main():
    cmd, *args = sys.argv[1:]
    {"cells": cmd_cells, "alert": cmd_alert, "labels": cmd_labels, "query": cmd_query}[cmd](*args)


main()
