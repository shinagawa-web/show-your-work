import json
import os

PICK_IDS = {20320, 5725001, 46404, 20438, 20384, 20440, 3856201, 20663, 20447}
PICK_WORDS = ('abort', 'interrupt', 'conflict', 'lock', 'slow query', 'killed')


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(x) for x in f if x.strip()]


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            for kv in line.split():
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    env[k] = v
    return env


def picked(o, payload):
    if not isinstance(o, dict) or not isinstance(o.get('t'), dict) or 'id' not in o:
        return None
    msg = o.get('msg', '')
    if o['id'] in PICK_IDS:
        return 'items' if 'test.items' in payload or o['id'] in (20440, 20447) else None
    if o['id'] == 20345 and 'test.items' in payload:
        return 'items'
    if msg.startswith('Index build') and 'test.items' in payload:
        return 'items'
    if any(w in msg.lower() for w in PICK_WORDS):
        return 'word'
    if o['id'] == 22944 and (o.get('attr') or {}).get('remote', '').startswith('172.17.0.1:'):
        return 'conn'
    return None


def analyse(run_dir, ts, invalid, h):
    r = {'name': os.path.basename(run_dir)}
    need = ('seed.jsonl', 'test.jsonl', 'listindexes.jsonl', 'container.log', 'docker-run.env')
    for n in need:
        if not os.path.exists(os.path.join(run_dir, n)):
            invalid.append(f'{run_dir}: {n} missing')
            return None
    evp = os.path.join(run_dir, 'events.jsonl')
    ev = h['load_events'](evp) if os.path.exists(evp) else []
    t0, _ = h['container_t0'](run_dir, ev)
    if t0 is None:
        invalid.append(f'{run_dir}: no start event and no StartedAt')
        return None
    r['t0'] = t0

    def rel(t):
        return None if t is None else float(t) - t0

    r['rel'] = rel
    healthy = [t for t, a in ev if a == 'health_status: healthy']
    r['healthy'] = rel(healthy[0]) if healthy else None
    rw, _ = h['load_runner_wait'](run_dir)
    rw = rw or []
    seen = [t for t, k, x in rw if k == 'log' and x.endswith('service is healthy.')]
    r['runner_healthy'] = rel(seen[0]) if seen else None
    r['steps'] = {k: rel(v) for k, v in load_env(os.path.join(run_dir, 'steps.env')).items()} if os.path.exists(os.path.join(run_dir, 'steps.env')) else {}
    env = load_env(os.path.join(run_dir, 'docker-run.env'))
    r['env'] = env
    for k in ('t_seed_start', 't_seed_end', 't_test_start', 't_test_end', 't_probe_start', 't_probe_end'):
        r[k] = rel(env[k]) if k in env else None
    r['seed_rc'] = env.get('seed_rc')
    r['test_rc'] = env.get('test_rc')
    r['seed'] = load_jsonl(os.path.join(run_dir, 'seed.jsonl'))
    r['test'] = load_jsonl(os.path.join(run_dir, 'test.jsonl'))
    r['probe'] = load_jsonl(os.path.join(run_dir, 'listindexes.jsonl'))
    for k in ('seed.err', 'test.err', 'listindexes.err'):
        r[k] = open(os.path.join(run_dir, k), errors='replace').read() if os.path.exists(os.path.join(run_dir, k)) else ''
    lo = r['t_seed_start'] if r['t_seed_start'] is not None else 0
    hi = (r['t_test_end'] if r['t_test_end'] is not None else 1e9) + 2
    lines = []
    drivers = {}
    with open(os.path.join(run_dir, 'container.log'), errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            t, _, payload = line.partition(' ')
            if not payload.startswith('{'):
                continue
            try:
                o = json.loads(payload)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get('id') == 51800:
                drivers[o.get('ctx')] = (((o.get('attr') or {}).get('doc') or {}).get('driver') or {}).get('name')
            why = picked(o, payload)
            if why is None:
                continue
            try:
                tt = rel(ts(t))
            except ValueError:
                continue
            if lo - 0.5 <= tt <= hi:
                lines.append((tt, o['id'], o.get('msg', ''), why, payload, drivers.get(o.get('ctx'), o.get('ctx'))))
    r['mongod'] = lines
    with open(os.path.join(run_dir, 'mongod-picked.log'), 'w') as f:
        for tt, i, m, why, payload, drv in lines:
            f.write(f'{tt:.3f}\t{drv}\t{payload}\n')
    return r


def first(evs, **kw):
    for e in evs:
        if all(e.get(k) == v for k, v in kw.items()):
            return e
    return None


def f(x):
    return '-' if x is None else f'{x:.3f}'


def err_text(e):
    return f"`{e.get('class')}`: `{e.get('message')}`"


def mongod_first(r, i, needle=None):
    x = [tt for tt, ii, m, why, p, drv in r['mongod'] if ii == i and why == 'items' and (needle is None or needle in p)]
    return x[0] if x else None


def mongod_done(r):
    return mongod_first(r, 20345, '"index":"name_1"')


def find_text(r):
    o = first(r['test'], ev='op', op='find')
    if o is None:
        return 'not run'
    return f"ok {json.dumps(o['result'])}" if o['ok'] else 'failed ' + err_text(o['error'])


def probe_text(r):
    o = first(r['probe'], ev='listIndexes')
    if o is None:
        return 'not run'
    if not o['ok']:
        return 'failed ' + err_text(o['error'])
    return ', '.join('`' + i['name'] + '`' for i in o['indexes'])


def ci_text(r, proc):
    e = first(r[proc], ev='createIndex-done')
    if e is None:
        return '-'
    rel = r['rel']
    return f"{f(rel(e['t']))} ok" if e['ok'] else f"{f(rel(e['t']))} {err_text(e['error'])}"


def returned_before_disconnect(r):
    evs = r['seed']
    idx_done = next((k for k, e in enumerate(evs) if e['ev'] == 'createIndex-done'), None)
    idx_last = next((k for k, e in enumerate(evs) if e['ev'] == 'op' and e['op'] == 'create10'), None)
    if idx_done is None or idx_last is None:
        return None
    return idx_done < idx_last


def yn(x):
    return '-' if x is None else ('yes' if x else 'no')


def errors_of(r):
    out = []
    rel = r['rel']
    for proc in ('seed', 'test'):
        for e in r[proc]:
            if e['ev'] == 'op' and not e['ok']:
                out.append((proc, rel(e['t_start']), f"`{e['op']}` failed: {err_text(e['error'])}"))
            elif e['ev'] in ('createIndex-done', 'model-index') and not e['ok']:
                out.append((proc, rel(e['t']), f"`{e['ev']}` event: {err_text(e['error'])}"))
            elif e['ev'] in ('model-error', 'unhandledRejection', 'uncaughtException', 'init-rejected', 'connection-error'):
                out.append((proc, rel(e['t']), f"`{e['ev']}`: {err_text(e['error'])}"))
            elif e['ev'] == 'exit' and e['code'] != 0:
                out.append((proc, rel(e['t']), f"process exit code {e['code']}"))
        if r[proc + '.err'].strip():
            out.append((proc, None, 'stderr: ```' + r[proc + '.err'].strip() + '```'))
    return out


def section(results, ts, invalid, h):
    d = os.path.join(results, 'mongoose')
    if not os.path.isdir(d):
        return [], []
    names = os.listdir(d)
    plain = sorted((n for n in names if n.startswith('run')), key=lambda n: int(n[3:]))
    debug = sorted((n for n in names if n.startswith('debug-run')), key=lambda n: int(n[9:]))
    plain = [x for x in (analyse(os.path.join(d, n), ts, invalid, h) for n in plain) if x]
    debug = [x for x in (analyse(os.path.join(d, n), ts, invalid, h) for n in debug) if x]
    out = []
    p = out.append
    p('## mongoose seed step, then test step')
    p('')
    p('### job: mongoose')
    p('')
    p('Seed process: `mongoose.connect`, 10 x `Model.create`, `mongoose.disconnect`. Test process: `mongoose.connect`, `Model.find`, `mongoose.disconnect`. Both define the same model with `{ name: { type: String, index: true } }` at module load. mongoose debug off.')
    p('')
    p('`createIndex` sent and returned are the model events `index-single-start` and `index-single-done` in the seed process. mongod lines: id 20438 `Index build: registering` and id 20345 `Index build: done building` with `"index":"name_1"`. `listIndexes` is run on `test.items` from a separate client right after the seed process exits and before the test process starts.')
    p('')
    p('| run | runner wait saw healthy | seed start | seed `disconnect` called | seed end (exit code) | `createIndex` sent | `createIndex` returned | returned before `disconnect` called | mongod 20438 | mongod 20345 `name_1` | `listIndexes` | test start | `find` |')
    p('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for r in plain:
        rel = r['rel']
        cs = first(r['seed'], ev='createIndex-start')
        dc = first(r['seed'], ev='op', op='disconnect')
        p(f"| {r['name']} | {f(r['runner_healthy'])} | {f(r['t_seed_start'])} | {f(rel(dc['t_start'])) if dc else '-'} | {f(r['t_seed_end'])} ({r['seed_rc']}) | {f(rel(cs['t'])) if cs else '-'} | {ci_text(r, 'seed')} | {yn(returned_before_disconnect(r))} | {f(mongod_first(r, 20438))} | {f(mongod_done(r))} | {probe_text(r)} | {f(r['t_test_start'])} | {find_text(r)} |")
    p('')
    p(f"Runs where `find` failed: {sum(1 for r in plain if not (first(r['test'], ev='op', op='find') or {}).get('ok'))} / {len(plain)}")
    p(f"Runs where the seed `createIndex` had not returned when `disconnect` was called: {sum(1 for r in plain if returned_before_disconnect(r) is False)} / {len(plain)}")
    p('')
    p('"returned before `disconnect` called" is from the order of events in the seed process: `index-single-done` before the end of the tenth `create`.')
    p('')
    any_err = False
    for r in plain:
        errs = errors_of(r)
        if not errs:
            continue
        any_err = True
        p(f"#### {r['name']}: errors")
        p('')
        for proc, t, text in errs:
            p(f"- {proc} process, {f(t)}: {text}")
        p('')
        p(f"mongod lines from seed start to test end ({r['name']}, picked ids only; time from container start, the client driver of the connection or the mongod thread, then the `docker logs` line):")
        p('')
        p('```')
        for tt, i, m, why, payload, drv in r['mongod']:
            p(f'{tt:.3f} [{drv}] {payload}')
        p('```')
        p('')
    if not any_err:
        p('No operation failed and no error was reported in either process in any run.')
        p('')

    notused = []
    q = notused.append
    if debug:
        q('### job: mongoose, debug on')
        q('')
        q('Same flow with `mongoose.set(\'debug\', fn)`. Times are when mongoose called the debug function.')
        q('')
        q('| run | runner wait saw healthy | seed debug calls (method at time) | seed `createIndex` returned | returned before `disconnect` called | seed `disconnect` called | mongod 20438 | mongod 20345 `name_1` | `listIndexes` | test debug calls | `find` |')
        q('|---|---|---|---|---|---|---|---|---|---|---|')
        for r in debug:
            rel = r['rel']
            dc = first(r['seed'], ev='op', op='disconnect')
            sd = ', '.join(f"{e['method']} {f(rel(e['t']))}" for e in r['seed'] if e['ev'] == 'debug')
            td = ', '.join(f"{e['method']} {f(rel(e['t']))}" for e in r['test'] if e['ev'] == 'debug')
            q(f"| {r['name']} | {f(r['runner_healthy'])} | {sd} | {ci_text(r, 'seed')} | {yn(returned_before_disconnect(r))} | {f(rel(dc['t_start'])) if dc else '-'} | {f(mongod_first(r, 20438))} | {f(mongod_done(r))} | {probe_text(r)} | {td} | {find_text(r)} |")
        q('')
        for r in debug:
            errs = errors_of(r)
            if errs:
                q(f"- {r['name']} errors: " + '; '.join(f'{proc} {f(t)} {text}' for proc, t, text in errs))
        q('')
    for r in plain + debug:
        rel = r['rel']
        q(f"### job: mongoose, {r['name']}")
        q('')
        q(f"- healthy (docker events): {f(r['healthy'])}; runner wait saw healthy: {f(r['runner_healthy'])}")
        q(f"- shell: seed {f(r['t_seed_start'])} -> {f(r['t_seed_end'])} rc {r['seed_rc']}; listIndexes probe {f(r['t_probe_start'])} -> {f(r['t_probe_end'])}; test {f(r['t_test_start'])} -> {f(r['t_test_end'])} rc {r['test_rc']}")
        for proc in ('seed', 'probe', 'test'):
            q(f'- {proc} process events:')
            for e in r[proc]:
                t = e.get('t', e.get('t_start'))
                rest = {k: v for k, v in e.items() if k not in ('ev', 't', 't_start', 't_end')}
                end = f" -> {f(rel(e['t_end']))}" if 't_end' in e else ''
                q(f"  - {f(rel(t))}{end} `{e['ev']}` `{json.dumps(rest)}`")
        ids = {}
        for tt, i, m, why, payload, drv in r['mongod']:
            ids.setdefault((i, m, drv), 0)
            ids[(i, m, drv)] += 1
        q('- mongod ids picked (id, msg, client driver or thread): ' + ', '.join(f'{i} `{m}` {d} x{n}' for (i, m, d), n in ids.items()))
        q('')
    return out, notused
