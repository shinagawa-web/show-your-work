import json
import os
import sys

root = sys.argv[1]
targets = sys.argv[2:]
here = os.path.dirname(root.rstrip('/'))

with open(os.path.join(here, 'conditions.tsv')) as f:
    lines = [l.rstrip('\n').split('\t') for l in f if l.strip()]
cols = lines[0]
defs = {row[0]: dict(zip(cols, row)) for row in lines[1:]}

NOISE = {'pause measured (s)', 'load rate (req/s)'}


def reps(cond):
    d = os.path.join(root, cond)
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        fp = os.path.join(d, name, 'analysis.json')
        if os.path.exists(fp):
            with open(fp) as f:
                out.append((name, json.load(f)))
    return out


def invalid(cond, a):
    c, df = a['config'], defs[cond]
    why = []
    if c['app LISTEN Send-Q'] != [int(df['backlog'])]:
        why.append(f"Send-Q {c['app LISTEN Send-Q']} != backlog {df['backlog']}")
    if c['app net.ipv4.tcp_abort_on_overflow'] != df['abort_on_overflow']:
        why.append('tcp_abort_on_overflow')
    if c['nginx net.ipv4.tcp_syn_linear_timeouts'] != df['syn_linear_timeouts']:
        why.append('tcp_syn_linear_timeouts')
    if c['nginx proxy_connect_timeout'] != [df['proxy_connect_timeout']]:
        why.append('proxy_connect_timeout')
    if c['nginx worker_processes'] != ['4'] or c['nginx keepalive directives'] != []:
        why.append('worker_processes or keepalive')
    if abs(c['pause measured (s)'] - float(df['pause_s'])) > 0.05:
        why.append(f"pause measured {c['pause measured (s)']}")
    if c['load requests'] != 3000:
        why.append(f"load requests {c['load requests']}")
    if not a['capture nginx side']['packets'] or not a['capture app side']['packets']:
        why.append('empty capture')
    return why


def compact(d):
    return ', '.join(f'{k}: {v}' for k, v in d.items()) if d else '(none)'


def dist_line(x):
    if not x or not x['count']:
        return 'n=0'
    return f"n={x['count']} min={x['min']} p50={x['p50']} p90={x['p90']} p99={x['p99']} max={x['max']}"


def row(label, values):
    return f"| {label} | " + ' | '.join(str(v).replace('|', '/') for v in values) + ' |'


out = []
for cond in targets:
    if cond == 'somaxconn':
        out.append('## somaxconn\n')
        out.append('```')
        with open(os.path.join(root, 'somaxconn', 'somaxconn.txt')) as f:
            out.append(f.read().rstrip())
        out.append('```\n')
        continue
    df = defs[cond]
    rs = reps(cond)
    out.append(f'## {cond}\n')
    out.append('definition: ' + ', '.join(f'{k}={df[k]}' for k in cols[2:]))
    src = df['from']
    if src in defs:
        changed = [k for k in cols[2:] if defs[src][k] != df[k]]
        out.append(f'from: {src}; columns that differ in conditions.tsv: {changed}')
        base = reps(src)
        for (name, a), (bname, b) in zip(rs, base):
            diff = {k: (b['config'][k], a['config'][k]) for k in a['config']
                    if k not in NOISE and a['config'][k] != b['config'][k]}
            out.append(f'observed config differences {src}/{bname} -> {cond}/{name}: {diff}')
        if not base:
            out.append(f'observed config of {src} not in this results directory')
    else:
        out.append('from: (starting point)')
    for name, a in rs:
        why = invalid(cond, a)
        out.append(f'{name}: ' + (f'RUN INVALID: {why}' if why else 'config matches definition'))
    out.append('')
    names = [n for n, _ in rs]
    A = [a for _, a in rs]
    out.append('| | ' + ' | '.join(names) + ' |')
    out.append('|---|' + '---|' * len(names))
    out.append(row('pause measured (s)', [a['config']['pause measured (s)'] for a in A]))
    out.append(row('load requests / rate', [f"{a['config']['load requests']} / {a['config']['load rate (req/s)']}" for a in A]))
    out.append(row('app Send-Q / app somaxconn', [f"{a['config']['app LISTEN Send-Q']} / {a['config']['app net.core.somaxconn']}" for a in A]))
    out.append(row('established before pause (total, per worker)', [f"{a['established before pause']['total']} {a['established before pause']['per worker']}" for a in A]))
    out.append(row('nginx requests', [a['nginx requests'] for a in A]))
    out.append(row('nginx status', [compact(a['nginx status']) for a in A]))
    out.append(row('requests arrived during pause', [a['requests arrived during pause'] for a in A]))
    out.append(row('status of those', [compact(a['status of requests arrived during pause']) for a in A]))
    out.append(row('metric A: 504 / all requests', [a['metric A: 504 / all requests'] for a in A]))
    out.append(row('metric A: 504 / arrived during pause', [a['metric A: 504 / requests arrived during pause'] for a in A]))
    out.append(row('504 upstream_connect_time', [compact(a['504 upstream_connect_time']) for a in A]))
    out.append(row('504 found in app log', [a['504 found in app log'] for a in A]))
    out.append(row('502 count / found in app log', [f"{a['502 count']} / {a['502 found in app log']}" for a in A]))
    out.append(row('nginx error log lines', [a['nginx error log lines'] for a in A]))
    k = 'metric B: response time minus SIGCONT, requests arrived during pause (s)'
    for st in sorted({s for a in A for s in a[k]}):
        out.append(row(f'metric B ({st})', [dist_line(a[k].get(st)) for a in A]))
    out.append(row('request_time of 200 arrived during pause (s)', [dist_line(a['request_time of 200 requests arrived during pause (s)']) for a in A]))
    out.append(row('upstream_connect_time rounded 0.1s', [compact(a['upstream_connect_time rounded to 0.1s (all numeric)']) for a in A]))
    out.append(row('upstream_connect_time "-"', [a['upstream_connect_time "-" count'] for a in A]))
    out.append(row('uct >= 0.5s: count / in app log / app ms max', [
        f"{a['upstream_connect_time >= 0.5s']['count']} / {a['upstream_connect_time >= 0.5s']['found in app log']} / {a['upstream_connect_time >= 0.5s']['app processing ms max']}" for a in A]))
    out.append(row('nstat delta', [compact(a['nstat delta']) for a in A]))
    out.append(row('Recv-Q max / Send-Q', [f"{a['ss LISTEN :8081']['Recv-Q max']} / {compact(a['ss LISTEN :8081']['Send-Q'])}" for a in A]))
    for side in ('capture nginx side', 'capture app side'):
        for key in ('SYN to :8081', 'SYN-ACK from :8081', 'SYN answered by SYN-ACK', 'SYN not answered by SYN-ACK',
                    'connections with at least one unanswered SYN', 'connections with more than one SYN-ACK',
                    'RST from :8081', 'RST to :8081'):
            out.append(row(f'{side}: {key}', [a[side][key] for a in A]))
    for key in ('connections by SYNs sent', 'SYN retransmission intervals rounded 0.1s'):
        out.append(row(f'capture nginx side: {key}', [compact(a['capture nginx side'][key]) for a in A]))
    out.append(row('504 connections / 200 with uct >= 0.5s', [f"{a['nginx status'].get('504', 0)} / {a['upstream_connect_time >= 0.5s']['count']}" for a in A]))
    for key in A[0]['P1'] if A else []:
        out.append(row(f'P1: {key}', [a['P1'][key] for a in A]))
    out.append('')
    for name, a in rs:
        out.append(f'{name} error log messages: {compact(a["nginx error log messages"])}')
        for st, x in a[k].items():
            out.append(f'{name} metric B ({st}) rounded 0.1s: {compact(x["rounded_0.1s"])}')
    out.append('')

print('\n'.join(out))
