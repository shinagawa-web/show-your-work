import json
import os
from datetime import datetime, timezone

SAMPLE_MSGS = ('loading sample data', 'downloading sample dataset...', 'sample dataset downloaded',
               'restoring sample dataset...', 'sample dataset restored', 'skipping: not loading sample data')
OOM_RUNNER_FROM = 'error running command'


def f(x):
    return '-' if x is None else f'{x:.2f}'


def err_text(e):
    return f"`{e.get('class')}`: `{e.get('message')}`"


def dmesg_ts(s):
    s = s.replace(',', '.')
    base, _, frac = s.partition('.')
    frac = frac.split('+')[0]
    return datetime.strptime(base, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp() + float('0.' + (frac or '0'))


def read(path):
    return open(path, errors='replace').read() if os.path.exists(path) else ''


def mongod_lines(r, ids):
    out = []
    with open(os.path.join(r['dir'], 'container.log'), errors='replace') as fh:
        for line in fh:
            t, _, payload = line.rstrip('\n').partition(' ')
            if not payload.startswith('{'):
                continue
            try:
                o = json.loads(payload)
            except ValueError:
                continue
            if isinstance(o.get('t'), dict) and o.get('id') in ids:
                out.append((t, o, payload))
    return out


def generic_table(p, job, rs):
    p(f'### job: {job}')
    p('')
    p('| run | healthy (docker events) | runner `service is healthy.` | last `initialization phase completed` | last `server started` | test step start | `npm ci` end | first operation | failed operation |')
    p('|---|---|---|---|---|---|---|---|---|')
    for r in rs:
        st = r.get('steps', {})
        step = st.get('t_test_step_start')
        npm = st.get('t_npm_end')
        fo = '-' if not r['failed'] else f"`{r['failed']['op']}` at {f(r['failed']['t_start'] - r['t0'])}: {err_text(r['failed']['error'])}"
        p(f"| {r['name']} | {f(r['healthy'])} | {f(r['runner_healthy'])} | {f(r['last_init'])} | {f(r['last_started'])} | {f(step)} | {f(npm)} | {f(r['first_op'])} | {fo} |")
    p('')
    n = len(rs)
    p(f"Runs where an operation failed: {sum(1 for r in rs if r['failed'])} / {n}")
    p(f"Runs where healthy (docker events) came before the last `server started`: {sum(1 for r in rs if r['healthy'] is not None and r['last_started'] is not None and r['healthy'] < r['last_started'])} / {n}")
    p(f"Runs where the runner saw healthy before the last `server started`: {sum(1 for r in rs if r['runner_healthy'] is not None and r['last_started'] is not None and r['runner_healthy'] < r['last_started'])} / {n}")
    p('')


def auth_detail(p, rs):
    for r in rs:
        p(f"auth, {r['name']}: mongod starts, the `security` options they were started with, and the first `Successfully authenticated` of user `e2e` after each start (time from container start, then the `docker logs` line without its timestamp prefix):")
        p('')
        p('```')
        starts = mongod_lines(r, {21951, 5286306})
        seen_after = True
        for t, o, payload in starts:
            if o['id'] == 21951:
                p(f"{f(r['rel'](r['ts'](t)))} {payload}")
                seen_after = False
            elif not seen_after and (o.get('attr') or {}).get('user') == 'e2e':
                p(f"{f(r['rel'](r['ts'](t)))} {payload}")
                seen_after = True
        p('```')
        p('')
        conns = [(t, payload) for t, payload, o in r['runner_lines_raw'] if o.get('msg') == 'connecting...']
        p(f"auth, {r['name']}: image runner `connecting...` lines:")
        p('')
        p('```')
        for t, payload in conns:
            p(f'{f(t)} {payload}')
        p('```')
        p('')


def hsi_detail(p, rs):
    for r in rs:
        ex = [(a, b) for a, b in r['hc_execs'] if r['healthy'] is None or a <= r['healthy']]
        p(f"health-start-interval, {r['name']}: healthcheck execs up to healthy (exec_start, exec_die): {', '.join(f'({f(a)}, {f(b)})' for a, b in ex)}")
    p('')


def oom_detail(p, rs):
    for r in rs:
        d = r['dir']
        env = {}
        for line in read(os.path.join(d, 'oom.env')).splitlines():
            for kv in line.split():
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    env[k] = v
        go = float(env['t_hog_go']) if 't_hog_go' in env else None
        p(f"#### mongot-oom, {r['name']}")
        p('')
        p(f"- memory limit and memory hog (`oom.env`): `limit_kb={env.get('limit_kb')} total_rss_kb={env.get('total_rss_kb')} mongot_rss_kb={env.get('mongot_rss_kb')} hog_kb={env.get('hog_kb')}`, `mongod_oom_score_adj={env.get('mongod_oom_score_adj')}`, `hog_oom_score_adj={env.get('hog_oom_score_adj')}`")
        p(f"- hog started: {f(r['rel'](go)) if go else '-'}; mongot gone and `oom_kill` raised, as polled: {f(r['rel'](float(env['t_oom_seen']))) if 't_oom_seen' in env else '-'}")
        before = read(os.path.join(d, 'memory.events.before')).split()
        after = read(os.path.join(d, 'memory.events.after')).split()
        p(f"- `memory.events` before the hog: `{' '.join(before)}`; after: `{' '.join(after)}`")
        try:
            st = json.load(open(os.path.join(d, 'inspect.json')))[0]['State']
            p(f"- `docker inspect` `.State` at collect: `Status={st.get('Status')} OOMKilled={st.get('OOMKilled')} ExitCode={st.get('ExitCode')} FinishedAt={st.get('FinishedAt')} Health.Status={(st.get('Health') or {}).get('Status')}`")
        except (ValueError, KeyError, IndexError, FileNotFoundError):
            pass
        hp = [json.loads(x) for x in read(os.path.join(d, 'health-poll.jsonl')).splitlines() if x.strip()]
        trans = []
        last = None
        for x in hp:
            s = ((x.get('health') or {}).get('Status'), x.get('mongot_pid') or '-')
            if s != last:
                trans.append(f"{f(r['rel'](x['t']))} {s[0]} mongot_pid={s[1]}")
                last = s
        p(f"- `.State.Health` polled every 2 s after the test (changes only): {'; '.join(trans) if trans else '-'}")
        p(f"- health_status events: {', '.join(f'{f(t)} {a}' for t, a in r['health_events'])}")
        p('')
        p('dmesg lines from the hog start (`sudo dmesg --time-format iso`, lines with `oom` or `Killed process`):')
        p('')
        p('```')
        for line in read(os.path.join(d, 'dmesg.txt')).splitlines():
            ts_, _, rest = line.partition(' ')
            try:
                t = dmesg_ts(ts_)
            except ValueError:
                continue
            if go and t >= go - 1 and ('oom' in rest.lower() or 'Killed process' in rest):
                p(line)
        p('```')
        p('')
        p('Image runner lines and mongod `Received signal` lines from the hog start (time from container start, then the `docker logs` line):')
        p('')
        p('```')
        for t, payload, o in r['all_lines']:
            if go and t >= r['rel'](go) and ((o.get('level') and o.get('time')) or o.get('id') in (23377, 23378, 20565)):
                p(f'{f(t)} {payload}')
        p('```')
        p('')
        p('Test operations:')
        p('')
        for o in r['ops']:
            p(f"- {f(o['t_start'] - r['t0'])} -> {f(o['t_end'] - r['t0'])} `{o['op']}` {'ok' if o['ok'] else 'failed ' + err_text(o['error'])}")
        p('')


def sample_detail(p, rs):
    for r in rs:
        p(f"sample-data, {r['name']}: image runner sample data lines, runner wait and health_status events (time from container start):")
        p('')
        for t, payload, o in r['runner_lines_raw']:
            if o.get('msg') in SAMPLE_MSGS:
                p(f"- {f(t)} `{o['msg']}`")
        for t, k, x in r['runner_wait']:
            if k == 'log':
                p(f'- {f(t)} runner: `{x}`')
        p(f"- health_status events: {', '.join(f'{f(t)} {a}' for t, a in r['health_events'])}")
        p('')


def section(runs, not_run, ts):
    out = []
    p = out.append
    jobs = [j for j in ('auth', 'health-start-interval', 'mongot-oom', 'sample-data') if runs.get(j) or not_run.get(j)]
    if not jobs:
        return out
    for j in jobs:
        for r in runs.get(j, []):
            r['ts'] = ts
            raw = []
            alls = []
            with open(os.path.join(r['dir'], 'container.log'), errors='replace') as fh:
                for line in fh:
                    t, _, payload = line.rstrip('\n').partition(' ')
                    if not payload.startswith('{'):
                        continue
                    try:
                        o = json.loads(payload)
                        tt = r['rel'](ts(t))
                    except ValueError:
                        continue
                    if not isinstance(o, dict):
                        continue
                    alls.append((tt, payload, o))
                    if 'level' in o and 'time' in o:
                        raw.append((tt, payload, o))
            r['runner_lines_raw'] = raw
            r['all_lines'] = alls
    p('## Conditions added on GitHub Actions')
    p('')
    for j in jobs:
        rs = runs.get(j, [])
        if rs:
            generic_table(p, j, rs)
        for name, lines in not_run.get(j, []):
            p(f'{j}, {name}: no step ran. GitHub Actions job log lines of the service wait:')
            p('')
            p('```')
            for line in lines:
                p(line)
            p('```')
            p('')
        if j == 'auth':
            auth_detail(p, rs)
        elif j == 'health-start-interval':
            hsi_detail(p, rs)
        elif j == 'mongot-oom':
            oom_detail(p, rs)
        elif j == 'sample-data':
            sample_detail(p, rs)
    return out
