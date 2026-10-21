import collections
import json
import os
import re
import subprocess
import sys

d = sys.argv[1]


def p(name):
    return os.path.join(d, name)


def read(name):
    with open(p(name)) as f:
        return f.read()


def num(s):
    try:
        return float(s)
    except ValueError:
        return None


def hist01(values):
    return {f'{k:.1f}': v for k, v in sorted(collections.Counter(round(x, 1) + 0.0 for x in values).items())}


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 3)


def dist(values):
    return {
        'count': len(values),
        'min': round(min(values), 3) if values else None,
        'p50': pct(values, 0.50),
        'p90': pct(values, 0.90),
        'p99': pct(values, 0.99),
        'max': round(max(values), 3) if values else None,
        'rounded_0.1s': hist01(values),
    }


cond = dict(l.split('=', 1) for l in read('condition.env').splitlines() if '=' in l)
backlog = int(cond['backlog'])
pause_nominal = float(cond['pause_s'])
rate = int(cond['rate'])

tl = {}
for l in read('timeline.txt').splitlines():
    k, v = l.split()
    tl[k] = float(v)
stop, cont = tl['stop'], tl['cont']

est_lines = read('established-before-pause.txt').splitlines()
workers = [int(x) for x in est_lines[0].split()[1:]]
socks = [l for l in est_lines[1:] if l.strip()]
by_pid = collections.Counter(int(m) for m in re.findall(r'pid=(\d+)', '\n'.join(socks)))
est_total = len(socks)
est_per_worker = [by_pid.get(w, 0) for w in workers]


def nstat(name):
    vals = {}
    for l in read(name).splitlines():
        parts = l.split()
        if len(parts) >= 2 and parts[0].startswith('TcpExt'):
            vals[parts[0]] = int(parts[1])
    return vals


ns0, ns1 = nstat('nstat-before.txt'), nstat('nstat-after.txt')
ns_delta = {k: ns1[k] - ns0.get(k, 0) for k in ns1}

sendq = collections.Counter()
recvq_max = 0
recvq_full_samples = 0
ss_samples = 0
for l in read('ss-listen.txt').splitlines():
    parts = l.split()
    if len(parts) < 4 or parts[1] != 'LISTEN':
        continue
    ss_samples += 1
    rq, sq = int(parts[2]), int(parts[3])
    sendq[sq] += 1
    recvq_max = max(recvq_max, rq)
    if rq == sq + 1:
        recvq_full_samples += 1


def sysctls(name):
    out = {}
    for l in read(name).splitlines():
        m = re.match(r'^(net\.\S+) = (\S+)$', l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


app_sys = sysctls('config-app.txt')
front_sys = sysctls('config-front.txt')
nginx_t = read('nginx-T.txt')


def directive(name):
    return re.findall(rf'^\s*{name}\s+([^;{{]+);', nginx_t, re.M)


app_stderr = read('app-stderr.log')
m_bl = re.search(r'backlog=(\d+)', app_stderr)
m_ver = re.search(r'nginx version: (\S+)', nginx_t)
vr = read('vegeta-report.txt')
m_req = re.search(r'Requests\s+\[total, rate, throughput\]\s+(\d+),\s+([\d.]+)', vr)
config = {
    'app listen backlog argument': int(m_bl.group(1)) if m_bl else None,
    'app LISTEN Send-Q': sorted(sendq),
    'app net.core.somaxconn': app_sys.get('net.core.somaxconn'),
    'app net.ipv4.tcp_abort_on_overflow': app_sys.get('net.ipv4.tcp_abort_on_overflow'),
    'nginx net.core.somaxconn': front_sys.get('net.core.somaxconn'),
    'nginx net.ipv4.tcp_syn_linear_timeouts': front_sys.get('net.ipv4.tcp_syn_linear_timeouts'),
    'nginx net.ipv4.tcp_syn_retries': front_sys.get('net.ipv4.tcp_syn_retries'),
    'nginx version': m_ver.group(1) if m_ver else None,
    'nginx worker_processes': directive('worker_processes'),
    'nginx proxy_connect_timeout': directive('proxy_connect_timeout'),
    'nginx keepalive directives': directive('keepalive'),
    'nginx upstream servers': directive('server'),
    'load requests': int(m_req.group(1)) if m_req else None,
    'load rate (req/s)': float(m_req.group(2)) if m_req else None,
    'pause measured (s)': round(cont - stop, 3),
}

rows = []
for l in read('nginx-access.log').splitlines():
    parts = l.split()
    kv = dict(x.split('=', 1) for x in parts[3:])
    msec = float(parts[0])
    rt = float(kv['rt'])
    rows.append({'msec': msec, 'id': parts[1], 'status': parts[2], 'uct': kv['uct'],
                 'uht': kv['uht'], 'rt': rt, 'arrival': msec - rt})

app_ms = {}
for l in read('app-access.log').splitlines():
    parts = l.split()
    app_ms[parts[1]] = float(parts[2])

status = collections.Counter(r['status'] for r in rows)
in_pause = [r for r in rows if stop <= r['arrival'] < cont]
in_pause_status = collections.Counter(r['status'] for r in in_pause)
r504 = [r for r in rows if r['status'] == '504']
r502 = [r for r in rows if r['status'] == '502']

err = read('nginx-error.log').splitlines()
shape = collections.Counter(re.sub(r'^\S+ \S+ \[(\w+)\] \d+#\d+: \*\d+ ', r'[\1] ', l) for l in err)

uct_num = [num(r['uct']) for r in rows]
uct_values = [u for u in uct_num if u is not None]
slow = [r for r in rows if num(r['uct']) is not None and num(r['uct']) >= 0.5]

after_cont = {'all': dist([r['msec'] - cont for r in in_pause])}
for st in sorted(in_pause_status):
    after_cont[st] = dist([r['msec'] - cont for r in in_pause if r['status'] == st])
rt_in_pause = dist([r['rt'] for r in in_pause if r['status'] == '200'])

PKT = re.compile(r'^(\d+\.\d+) IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): Flags \[([^\]]*)\]')


def capture(name):
    out = subprocess.run(['tcpdump', '-nn', '-tt', '-r', p(name)], capture_output=True, text=True)
    pk = []
    for l in out.stdout.splitlines():
        mm = PKT.match(l)
        if mm:
            pk.append((float(mm.group(1)), mm.group(2), int(mm.group(3)), mm.group(4), int(mm.group(5)), mm.group(6)))
    flows = collections.defaultdict(list)
    for t, sip, sp, dip, dp, fl in pk:
        if dp == 8081:
            key, frm = (sip, sp), 'client'
        elif sp == 8081:
            key, frm = (dip, dp), 'server'
        else:
            continue
        if 'R' in fl:
            kind = 'rst'
        elif fl == 'S':
            kind = 'syn'
        elif fl == 'S.':
            kind = 'synack'
        else:
            kind = 'other'
        flows[key].append((t, frm, kind))
    syn = synack = answered = unanswered = rst_server = rst_client = 0
    flows_with_drop = flows_multi_synack = 0
    per_conn = collections.Counter()
    intervals = collections.Counter()
    for ev in flows.values():
        drop = False
        syn_t = [t for t, f, k in ev if k == 'syn' and f == 'client']
        got = any(k == 'synack' for _, _, k in ev)
        per_conn[f"{len(syn_t)} SYN, {'SYN-ACK' if got else 'no SYN-ACK'}"] += 1
        for n, (a, b) in enumerate(zip(syn_t, syn_t[1:]), 1):
            intervals[f'retransmission {n}: {round(b - a, 1) + 0.0:.1f}s'] += 1
        for i, (_, f, k) in enumerate(ev):
            if k == 'syn' and f == 'client':
                syn += 1
                nxt = next((k2 for _, _, k2 in ev[i + 1:] if k2 in ('syn', 'synack')), None)
                if nxt == 'synack':
                    answered += 1
                else:
                    unanswered += 1
                    drop = True
            elif k == 'synack' and f == 'server':
                synack += 1
            elif k == 'rst':
                if f == 'server':
                    rst_server += 1
                else:
                    rst_client += 1
        flows_with_drop += drop
        if [k for _, _, k in ev].count('synack') > 1:
            flows_multi_synack += 1
    return {
        'packets': len(pk),
        'connections (4-tuples)': len(flows),
        'SYN to :8081': syn,
        'SYN-ACK from :8081': synack,
        'SYN answered by SYN-ACK': answered,
        'SYN not answered by SYN-ACK': unanswered,
        'connections with at least one unanswered SYN': flows_with_drop,
        'connections with more than one SYN-ACK': flows_multi_synack,
        'RST from :8081': rst_server,
        'RST to :8081': rst_client,
        'connections by SYNs sent': dict(sorted(per_conn.items())),
        'SYN retransmission intervals rounded 0.1s': dict(sorted(intervals.items())),
    }


cap_front = capture('front.pcap')
cap_app = capture('app.pcap')

result = {
    'condition': cond,
    'timeline': tl,
    'config': config,
    'established before pause': {'total': est_total, 'per worker': est_per_worker},
    'nstat before': ns0,
    'nstat after': ns1,
    'nstat delta': ns_delta,
    'ss LISTEN :8081': {'samples': ss_samples, 'Send-Q': dict(sendq), 'Recv-Q max': recvq_max,
                        'samples with Recv-Q = Send-Q + 1': recvq_full_samples},
    'nginx requests': len(rows),
    'nginx status': dict(status),
    'requests arrived during pause': len(in_pause),
    'status of requests arrived during pause': dict(in_pause_status),
    'metric A: 504 / all requests': round(len(r504) / len(rows), 4) if rows else None,
    'metric A: 504 / requests arrived during pause':
        round(in_pause_status.get('504', 0) / len(in_pause), 4) if in_pause else None,
    '504 upstream_connect_time': dict(collections.Counter(r['uct'] for r in r504)),
    '504 found in app log': sum(1 for r in r504 if r['id'] in app_ms),
    '502 count': len(r502),
    '502 upstream_connect_time': dict(collections.Counter(r['uct'] for r in r502)),
    '502 found in app log': sum(1 for r in r502 if r['id'] in app_ms),
    'nginx error log lines': len(err),
    'nginx error log messages': dict(shape.most_common()),
    'metric B: response time minus SIGCONT, requests arrived during pause (s)': after_cont,
    'request_time of 200 requests arrived during pause (s)': rt_in_pause,
    'upstream_connect_time rounded to 0.1s (all numeric)': hist01(uct_values),
    'upstream_connect_time "-" count': sum(1 for u in uct_num if u is None),
    'upstream_connect_time >= 0.5s': {
        'count': len(slow),
        'found in app log': sum(1 for r in slow if r['id'] in app_ms),
        'app processing ms max': max((app_ms[r['id']] for r in slow if r['id'] in app_ms), default=None),
    },
    'app access log lines': len(app_ms),
    'capture nginx side': cap_front,
    'capture app side': cap_app,
    'P1': {
        'established before pause': est_total,
        'rate x pause - (established + backlog + 1)': round(rate * pause_nominal - (est_total + backlog + 1), 1),
        'requests arrived during pause - (established + backlog + 1)': len(in_pause) - (est_total + backlog + 1),
        'connections with at least one unanswered SYN (app capture)': cap_app['connections with at least one unanswered SYN'],
    },
}

with open(p('analysis.json'), 'w') as f:
    json.dump(result, f, indent=1)
print(json.dumps(result, indent=1))
