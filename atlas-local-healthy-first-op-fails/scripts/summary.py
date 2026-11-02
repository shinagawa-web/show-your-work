import json
import os
import sys

import summary_conditions
import summary_mongoose
from datetime import datetime, timezone

results = sys.argv[1]
jobs = sys.argv[2:]
invalid = []
not_run = {}


def ts(s):
    date, frac = s.rstrip('Z').split('.')
    base = datetime.strptime(date, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
    return base + float('0.' + frac)


def load_events(path):
    ev = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                ev.append((e['timeNano'] / 1e9, e['Action']))
    return ev


def load_log(path):
    rows = []
    with open(path, errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            if ' ' not in line:
                continue
            t, payload = line.split(' ', 1)
            try:
                t = ts(t)
            except ValueError:
                continue
            obj = None
            if payload.startswith('{'):
                try:
                    obj = json.loads(payload)
                except ValueError:
                    obj = None
            rows.append((t, payload, obj))
    return rows


def kind(obj):
    if not isinstance(obj, dict):
        return None
    if 'level' in obj and 'time' in obj:
        return 'runner'
    if obj.get('svc') == 'MONGOT':
        return 'mongot'
    if isinstance(obj.get('t'), dict) and 'id' in obj:
        return 'mongod'
    return None


MONGOD_IDS = {4615611: 'mongod: MongoDB starting', 23016: 'mongod: Waiting for connections',
              23377: 'mongod: Received signal', 20565: 'mongod: Now exiting'}
MONGOT_MSGS = {'[Starting Mongot]': 'mongot: [Starting Mongot]',
               'Starting message server': 'mongot: Starting message server',
               'Received shutdown signal. Shutting down.': 'mongot: Received shutdown signal. Shutting down.'}
RUNNER_MARKS = ('replicaset initialized', 'seeding finished', 'initialization phase completed',
                'starting mongod process...', 'starting mongot process...', 'mongot is healthy', 'server started')


def load_runner_wait(run_dir):
    gh = os.path.join(run_dir, 'runner-job.log')
    if os.path.exists(gh):
        rows = []
        with open(gh, errors='replace') as f:
            for line in f:
                line = line.rstrip('\n').lstrip('\ufeff')
                t, _, text = line.partition(' ')
                text = text.removeprefix('##[group]')
                if not (text.startswith('Waiting for all services to be ready') or 'service is starting, waiting' in text
                        or text.endswith('service is healthy.') or text.startswith('Service container ')):
                    continue
                try:
                    rows.append((ts(t), 'log', text))
                except ValueError:
                    continue
        return rows, 'GitHub Actions job log'
    tsv = os.path.join(run_dir, 'runner-wait.tsv')
    if os.path.exists(tsv):
        with open(tsv) as f:
            return [(float(t), k, x) for t, k, x in (line.rstrip('\n').split('\t') for line in f if line.strip())], 'emulated'
    return None, None


def load_env(path):
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                for kv in line.split():
                    if '=' in kv:
                        k, v = kv.split('=', 1)
                        env[k] = v
    return env


def container_t0(run_dir, ev):
    starts = [t for t, a in ev if a == 'start']
    if starts:
        return starts[0], 'docker events start'
    ins = os.path.join(run_dir, 'inspect.json')
    if os.path.exists(ins):
        try:
            return ts(json.load(open(ins))[0]['State']['StartedAt']), 'docker inspect StartedAt'
        except (ValueError, KeyError, IndexError):
            pass
    return None, None


def analyse(run_dir):
    r = {'dir': run_dir, 'name': os.path.basename(run_dir)}
    gh = os.path.join(run_dir, 'runner-job.log')
    if not os.path.exists(os.path.join(run_dir, 'test.jsonl')) and os.path.exists(gh):
        lines = [x.rstrip('\n') for x in open(gh, errors='replace')
                 if 'service' in x.lower() and ('waiting' in x or 'healthy' in x or 'failed' in x.lower() or 'unhealthy' in x)]
        if any('failed' in x.lower() or 'unhealthy' in x for x in lines):
            not_run.setdefault(os.path.basename(os.path.dirname(run_dir)), []).append((r['name'], lines))
            return None
    for need in ('test.jsonl', 'container.log'):
        if not os.path.exists(os.path.join(run_dir, need)):
            inv = os.path.join(run_dir, 'invalid.txt')
            invalid.append(f'{run_dir}: {need} missing' + (': ' + open(inv).read().strip() if os.path.exists(inv) else ''))
            return None
    evp = os.path.join(run_dir, 'events.jsonl')
    ev = load_events(evp) if os.path.exists(evp) else []
    t0, r['t0_source'] = container_t0(run_dir, ev)
    if t0 is None:
        invalid.append(f'{run_dir}: no start event and no StartedAt')
        return None
    r['t0'] = t0

    def rel(t):
        return None if t is None else t - t0

    r['rel'] = rel
    healthy = [t for t, a in ev if a == 'health_status: healthy']
    r['healthy'] = rel(healthy[0]) if healthy else None
    hc = []
    cur = None
    for t, a in ev:
        if a.startswith('exec_start') and 'healthcheck' in a:
            cur = [rel(t), None]
        elif a == 'exec_die' and cur is not None:
            cur[1] = rel(t)
            hc.append(cur)
            cur = None
    r['hc_execs'] = hc
    r['health_events'] = [(rel(t), a) for t, a in ev if a.startswith('health_status')]
    rw, r['runner_wait_source'] = load_runner_wait(run_dir)
    if rw is None:
        rw = []
        if os.environ.get('REQUIRE_RUNNER_LOG') == '1':
            invalid.append(f'{run_dir}: no runner wait log')
    r['runner_wait'] = [(rel(t), k, x) for t, k, x in rw]
    seen = [t for t, k, x in rw if k == 'log' and x.endswith('service is healthy.')]
    r['runner_healthy'] = rel(seen[0]) if seen else None
    r['steps'] = {k: rel(float(v)) for k, v in load_env(os.path.join(run_dir, 'steps.env')).items()}
    r['run_env'] = load_env(os.path.join(run_dir, 'docker-run.env'))
    ops = []
    with open(os.path.join(run_dir, 'test.jsonl')) as f:
        for line in f:
            if line.strip():
                ops.append(json.loads(line))
    r['ops'] = [o for o in ops if o['op'] not in ('test', 'end')]
    r['driver'] = next((o.get('driver') for o in ops if o['op'] == 'test'), None)
    r['first_op'] = rel(r['ops'][0]['t_start']) if r['ops'] else None
    r['failed'] = next((o for o in r['ops'] if not o['ok']), None)
    log = load_log(os.path.join(run_dir, 'container.log'))
    r['runner_lines'] = [(rel(t), p, o) for t, p, o in log if kind(o) == 'runner']
    marks = []
    for t, p, o in log:
        k = kind(o)
        if k == 'runner' and o['msg'] in RUNNER_MARKS:
            marks.append((rel(t), 'runner: ' + o['msg']))
        elif k == 'mongod' and o.get('id') in MONGOD_IDS:
            marks.append((rel(t), MONGOD_IDS[o['id']]))
        elif k == 'mongot' and o.get('msg') in MONGOT_MSGS:
            marks.append((rel(t), MONGOT_MSGS[o['msg']]))
    r['marks'] = marks
    msgs = [(t, o['msg']) for t, p, o in r['runner_lines']]

    def find(m):
        return [t for t, x in msgs if x == m]

    r['init_completed'] = find('initialization phase completed')
    r['server_started'] = find('server started')
    r['seeding_finished'] = find('seeding finished')
    r['last_init'] = r['init_completed'][-1] if r['init_completed'] else None
    r['last_started'] = r['server_started'][-1] if r['server_started'] else None
    sf = r['seeding_finished'][0] if r['seeding_finished'] else None
    stops = [t for t, m in marks if sf is not None and t > sf and m in (
        'mongod: Received signal', 'mongot: Received shutdown signal. Shutting down.')]
    r['stop_after_seed'] = min(stops) if stops else None
    if r['last_started'] is None or (r['healthy'] is None and os.path.basename(os.path.dirname(run_dir)) != 'sample-data'):
        invalid.append(f'{run_dir}: no final server started or no healthy event')
    inv = os.path.join(run_dir, 'invalid.txt')
    if os.path.exists(inv):
        invalid.append(f'{run_dir}: ' + open(inv).read().strip())
    return r


def f(x):
    return '-' if x is None else f'{x:.2f}'


def err_text(o):
    e = o['error']
    return f"`{e['class']}`: `{e['message']}`"


def res_text(o):
    return err_text(o) if not o['ok'] else '`' + json.dumps(o['result']) + '`'


TEST_JOBS = ('wait', 'nowait', 'auth', 'health-start-interval', 'mongot-oom', 'sample-data')
runs = {}
for j in [x for x in jobs if x in TEST_JOBS]:
    d = os.path.join(results, j)
    names = sorted((n for n in os.listdir(d) if n.startswith('run')), key=lambda n: int(n[3:])) if os.path.isdir(d) else []
    runs[j] = [x for x in (analyse(os.path.join(d, n)) for n in names) if x is not None]

out = []
p = out.append
p('# atlas-local-healthy-first-op-fails')
p('')
p('Times are seconds from the container `start` event in `docker events`.')
p('')

p('## 2. Values and causes')
p('')
if 'wait' in runs:
    p('### job: wait')
    p('')
    p('First-start restart: operations that failed in the wait test.')
    p('')
    fails = [r for r in runs['wait'] if r['failed']]
    if not fails:
        p(f"No operation failed in {len(runs['wait'])} runs.")
    for r in fails:
        p(f"- {r['name']}: `{r['failed']['op']}` failed, {err_text(r['failed'])}")
    p('')
if 'nowait' in runs:
    p('### job: nowait')
    p('')
    p('Search index not yet queryable: `$search` right after `createSearchIndex`.')
    p('')
    p('| run | operation | start (s) | result |')
    p('|---|---|---|---|')
    for r in runs['nowait']:
        started = False
        for o in r['ops']:
            started = started or o['op'] == 'createSearchIndex'
            if started:
                p(f"| {r['name']} | `{o['op']}` | {f(o['t_start'] - r['t0'])} | {res_text(o)} |")
    p('')

if 'wait' in runs:
    p('## 3. Measurements per cause')
    p('')
    p('### job: wait')
    p('')
    p('| run | first mongod or mongot stop after `seeding finished` | last `initialization phase completed` | healthcheck exec that returned healthy (start, end) | healthy (docker events) | last `server started` | runner wait saw healthy | first operation starts | failed operation |')
    p('|---|---|---|---|---|---|---|---|---|')
    cnt = {'stop': 0, 'init': 0, 'started': 0}
    for r in runs['wait']:
        h = r['healthy']
        if h is not None:
            cnt['stop'] += r['stop_after_seed'] is not None and h < r['stop_after_seed']
            cnt['init'] += r['last_init'] is not None and h < r['last_init']
            cnt['started'] += r['last_started'] is not None and h < r['last_started']
        ex = [(a, b) for a, b in r['hc_execs'] if h is not None and a <= h]
        exs = f'({f(ex[-1][0])}, {f(ex[-1][1])})' if ex else '-'
        fo = '-' if not r['failed'] else f"`{r['failed']['op']}` at {f(r['failed']['t_start'] - r['t0'])}"
        p(f"| {r['name']} | {f(r['stop_after_seed'])} | {f(r['last_init'])} | {exs} | {f(h)} | {f(r['last_started'])} | {f(r['runner_healthy'])} | {f(r['first_op'])} | {fo} |")
    p('')
    n = len(runs['wait'])
    p(f"Runs where healthy came before the first stop after `seeding finished`: {cnt['stop']} / {n}")
    p(f"Runs where healthy came before the last `initialization phase completed`: {cnt['init']} / {n}")
    p(f"Runs where healthy came before the last `server started`: {cnt['started']} / {n}")
    p(f"Runs where an operation failed: {sum(1 for r in runs['wait'] if r['failed'])} / {n}")
    p('')
    if runs['wait']:
        r = runs['wait'][0]
        sf = r['seeding_finished'][0] if r['seeding_finished'] else None
        p(f"Image runner log lines from `seeding finished` to the last `server started` ({r['name']}, `docker logs` output with mongod and mongot lines removed):")
        p('')
        p('```')
        for t, payload, o in r['runner_lines']:
            if sf is not None and sf <= t <= r['last_started']:
                p(payload)
        p('```')
        p('')

    p('## 4. Using it in your CI')
    p('')
    p('### job: wait')
    p('')
    if runs['wait']:
        r = runs['wait'][0]
        p(f"First 12 image runner lines in `docker logs` with `RUNNER_LOG_FILE=/dev/stdout` ({r['name']}, mongod and mongot lines removed):")
        p('')
        p('```')
        for t, payload, o in r['runner_lines'][:12]:
            p(payload)
        p('```')
        p('')

mg_main, mg_notused = summary_mongoose.section(results, ts, invalid, {'load_events': load_events, 'container_t0': container_t0, 'load_runner_wait': load_runner_wait}) if 'mongoose' in jobs else ([], [])
out.extend(mg_main)
out.extend(summary_conditions.section(runs, not_run, ts))

p('## Not used for numbers')
p('')
for j in runs:
    for r in runs[j]:
        p(f"### job: {j}, {r['name']}")
        p('')
        p(f"- driver: mongodb {r['driver']}")
        execs = [(a, b) for a, b in r['hc_execs'] if r['healthy'] is None or a <= r['healthy']]
        p(f"- healthcheck execs up to healthy (exec_start, exec_die): {', '.join(f'({f(a)}, {f(b)})' for a, b in execs)}")
        p(f"- health_status events: {', '.join(f'{f(t)} {a}' for t, a in r['health_events'])}")
        p(f"- first mongod or mongot stop after `seeding finished`: {f(r['stop_after_seed'])}")
        p(f"- every `initialization phase completed`: {', '.join(f(t) for t in r['init_completed'])}; every `server started`: {', '.join(f(t) for t in r['server_started'])}")
        p('- GitHub Actions runner wait, emulated:')
        for t, k, x in r['runner_wait']:
            if k == 'log':
                p(f'  - {f(t)} {x}')
        p('- test operations (start -> end):')
        for o in r['ops']:
            p(f"  - {f(o['t_start'] - r['t0'])} -> {f(o['t_end'] - r['t0'])} `{o['op']}` {'ok' if o['ok'] else 'failed'} {res_text(o)}")
        p('- process marks:')
        for t, m in r['marks']:
            p(f'  - {f(t)} {m}')
        p('')

out.extend(mg_notused)

env = os.path.join(results, 'environment.txt')
if os.path.exists(env):
    p('### environment')
    p('')
    p('```')
    p(open(env).read().rstrip())
    p('```')
    p('')
tim = os.path.join(results, 'timing.tsv')
if os.path.exists(tim):
    p('### timing (s)')
    p('')
    p('```')
    p(open(tim).read().rstrip())
    p('```')
    p('')

if invalid:
    p('## Invalid')
    p('')
    for x in invalid:
        p(f'- {x}')
print('\n'.join(out))
sys.exit(1 if invalid else 0)
