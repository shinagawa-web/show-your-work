import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [results, ...names] = process.argv.slice(2);
const tsv = readFileSync(new URL('../conditions.tsv', import.meta.url), 'utf8').trim().split('\n');
const header = tsv[0].split('\t');
const conds = Object.fromEntries(
  tsv.slice(1).map((line) => {
    const row = Object.fromEntries(line.split('\t').map((v, i) => [header[i], v]));
    return [row.name, row];
  }),
);
const want = names.length ? names : Object.keys(conds);
const has = (n) => want.includes(n) && existsSync(join(results, n));
const read = (n, f) => {
  const p = join(results, n, f);
  return existsSync(p) ? readFileSync(p, 'utf8') : null;
};
const json = (n, f) => {
  const raw = read(n, f);
  if (raw === null) return null;
  const s = raw.startsWith('$ ') ? raw.slice(raw.indexOf('\n') + 1) : raw;
  try {
    return JSON.parse(s.slice(s.indexOf('{')));
  } catch {
    return null;
  }
};
const rc = (n, f) => {
  const s = read(n, `${f}.rc`);
  return s === null ? null : Number(s.trim());
};
const sha = (n, f) => read(n, f)?.trim() ?? null;
const tag = (n) => `${n} (job ${conds[n].job})`;
const invalid = [];

function turbo(n, col) {
  const log = read(n, `${col}.log`);
  if (log === null) return null;
  const all = log.split('\n');
  const summary = json(n, `${col}.summary.json`);
  const task = summary?.tasks?.find((t) => t.taskId === 'b#test') ?? null;
  const target = conds[n].target;
  const bCache = all.filter((l) => /^b:test: cache /.test(l));
  const hash = bCache[0]?.match(/([0-9a-f]{16})\s*$/)?.[1] ?? null;
  const code = rc(n, col);
  const failed = new RegExp(`^(b:test:)?\\s+FAIL\\s+test/${target}\\.test\\.ts`, 'm').test(log);
  let cell;
  if (!task || task.cache.status === 'HIT') cell = code === 0 ? 'not run (green)' : `not run (exit ${code})`;
  else cell = failed ? 'ran, failed' : code === 0 ? 'ran, passed' : `ran, exit ${code}`;
  const mode = all.filter((l) => /^b:test: APP_MODE seen by the test/.test(l));
  return { all, log, task, code, cell, bCache, hash, mode };
}

function correct(n) {
  const report = json(n, 'correct-b.json');
  if (!report) return null;
  const target = conds[n].target;
  const files = report.testResults ?? [];
  const hit = files.find((f) => f.name.endsWith(`/test/${target}.test.ts`));
  const others = files.filter((f) => f.status === 'failed' && f !== hit).map((f) => f.name);
  const cell = !hit ? 'not run' : hit.status === 'failed' ? 'ran, failed' : 'ran, passed';
  const mode = (read(n, 'correct-b.log') ?? '').split('\n').filter((l) => /APP_MODE seen by the test/.test(l));
  return { cell, others, mode };
}

function dryTask(n) {
  return json(n, 'dry.log')?.tasks?.find((t) => t.taskId === 'b#test') ?? null;
}

for (const n of want) {
  if (!has(n)) {
    invalid.push(`${n}: no results`);
    continue;
  }
  const bad = read(n, 'invalid.txt');
  if (bad) invalid.push(`${n}: ${bad.trim()}`);
  for (const col of ['cache-build', 'cache', 'force', 'cache-verbose', 'cache-gha']) {
    const t = turbo(n, col);
    if (t && t.code !== 0 && t.cell !== 'ran, failed') invalid.push(`${n}: ${col} exited ${t.code} without failing test/${conds[n].target}.test.ts`);
  }
  const c = correct(n);
  if (!c || c.cell !== 'ran, failed') invalid.push(`${n}: the full vitest run did not fail test/${conds[n].target}.test.ts (${c?.cell})`);
  if (c?.others.length) invalid.push(`${n}: other test files failed: ${c.others.join(', ')}`);
}

const out = [];
const p = (...s) => out.push(...s);
const block = (arr) => (!arr || arr.length === 0 ? ['    (no such line)'] : arr.map((l) => `    ${l}`));
const numbered = (arr, s, e) => arr.slice(s, e).map((l, k) => `${String(s + k + 1).padStart(3)}| ${l}`);

p('# turborepo-cache-misses', '');
p('The cache column is `turbo run test --summarize` with the cache built on main; the correct column is `vitest run` in packages/b without Turborepo. A cell is about the test file the condition breaks (conditions.tsv `target`).', '');

p('## 2. How the cache decides', '');
p('| Condition | Change | Cache | Correct |', '|---|---|---|---|');
for (const n of Object.keys(conds).filter(has)) {
  p(`| ${tag(n)} | ${conds[n].label} | ${turbo(n, 'cache')?.cell ?? 'n/a'} | ${correct(n)?.cell ?? 'n/a'} |`);
}
p('');

p('## 3. Reproduce and check', '');
const sub = (title) => p(`### ${title}`, '');
const item = (n, title, arr) => {
  if (has(n)) p(`${tag(n)}, ${conds[n].label}. ${title}`, '', ...block(arr), '');
};

sub('Source of the dependency (P2, P3)');
for (const n of ['P2', 'P3']) {
  const t = has(n) && turbo(n, 'cache');
  if (t) item(n, `\`turbo run test\`: ${t.cell}`, t.bCache);
}

sub('The leak stays on main and fails later (P4 to P6)');
const p4 = has('P4') ? turbo('P4', 'cache') : null;
if (p4) {
  const built = sha('P4', 'cache-build.sha');
  const head = sha('P4', 'head.sha');
  const s = p4.task?.cache?.sha ?? null;
  item('P4', `\`turbo run test\` on main: ${p4.cell}`, [
    ...p4.bCache,
    `Run Summary b#test cache.sha: ${s ?? '(absent)'}`,
    `commit the cache was built on (main before P2 was merged): ${built} (${s === built ? 'equal' : 'not equal'})`,
    `HEAD of main after P2 was merged: ${head} (${s === head ? 'equal' : 'not equal'})`,
  ]);
  p(`${tag('P4')}, commits from cache.sha to HEAD of main:`, '', ...block((read('P4', 'git-log.log') ?? '').trim().split('\n').filter(Boolean)), '');
  const f = turbo('P4', 'force');
  if (f) p(`${tag('P4')}, \`turbo run test --force\` on main: ${f.cell}, exit ${f.code}`, '', ...block(f.bCache), '');
}
for (const n of ['P5', 'P6']) {
  const t = has(n) && turbo(n, 'cache');
  if (t) {
    const cmp = p4?.hash && t.hash ? (p4.hash === t.hash ? 'the same' : 'different') : 'not comparable';
    item(n, `\`turbo run test\`: ${t.cell}. Its hash and the P4 cache hit hash (${p4?.hash ?? 'n/a'}) are ${cmp}`, t.bCache);
  }
}

sub('Files outside the package (P7, P8)');
for (const n of ['P7', 'P8']) {
  const t = has(n) && turbo(n, 'cache');
  if (t) item(n, `\`turbo run test\`: ${t.cell}`, t.bCache);
}

sub('Environment variables (P9, P10)');
for (const n of ['P9', 'P10']) {
  const t = has(n) && turbo(n, 'cache');
  if (!t) continue;
  item(n, `\`turbo run test\` with APP_MODE=new in the shell: ${t.cell}`, [...t.bCache, ...t.mode]);
  if (n === 'P9') {
    const f = turbo(n, 'force');
    if (f) p(`${tag(n)}, \`turbo run test --force\` with APP_MODE=new in the shell: ${f.cell}`, '', ...block([...f.bCache, ...f.mode]), '');
  }
}

p('## 4. Checking your own CI', '');
const dryItem = (n, key) => {
  const d = has(n) && dryTask(n);
  if (d) p(`${tag(n)}, \`turbo run test --dry=json\`, task \`b#test\`, \`${key}\`:`, '', ...block(JSON.stringify(d[key], null, 2).split('\n')), '');
};
dryItem('P2', 'dependencies');
dryItem('P7', 'inputs');
dryItem('P9', 'environmentVariables');
if (has('P4')) {
  const v = turbo('P4', 'cache-verbose');
  const line = v?.all.filter((l) => /cache hit produced by sha:/.test(l) && (!p4?.hash || l.startsWith(`${p4.hash}:`))) ?? [];
  p(`${tag('P4')}, \`turbo run test -v\` (${v?.cell}): the \`cache hit produced by sha:\` line for b#test ${line.length ? 'is printed' : 'is not printed'}`, '', ...block(line), '');
  const g = turbo('P4', 'cache-gha');
  if (g) {
    const s = g.all.findIndex((l) => l === '::group::b:test');
    const e = s < 0 ? -1 : g.all.findIndex((l, i) => i > s && l === '::endgroup::');
    const lines = s < 0 ? [] : [...numbered(g.all, Math.max(0, s - 1), s + 4), '...', ...(e < 0 ? ['(no ::endgroup:: after it)'] : numbered(g.all, e - 3, e + 2))];
    p(`${tag('P4')}, \`turbo run test\` with GITHUB_ACTIONS=true (${g.cell}): the start and the end of the b:test group (line numbers of cache-gha.log, "..." marks the lines left out)`, '', ...block(lines), '');
  }
}
if (has('P5')) {
  const g = turbo('P5', 'cache-gha');
  if (g) {
    const h = g.all.findIndex((l) => l === 'b:test');
    const lines = h < 0 ? [] : numbered(g.all, Math.max(0, h - 2), h + 4);
    p(`${tag('P5')}, \`turbo run test\` with GITHUB_ACTIONS=true (${g.cell}, exit ${g.code}): the header of the failed b:test task and the cache miss line (line numbers of cache-gha.log, escape sequences removed)`, '', ...block(lines), '');
  }
}

p('## Numbers not used', '');
p('Versions:', '', ...block(readFileSync(join(results, 'versions.txt'), 'utf8').trim().split('\n')), '');
const roots = want.filter((k) => has(k) && conds[k].after === '-');
const passed = roots.filter((k) => rc(k, 'cache-build') === 0);
p(`- Cache built on main before the change (\`turbo run test\`, APP_MODE=old): exit 0 in ${passed.length} of ${roots.length} conditions${passed.length === roots.length ? '' : ` (not: ${roots.filter((k) => !passed.includes(k)).join(', ')})`}`);
p('');

if (invalid.length) p(...invalid.map((s) => `RUN INVALID: ${s}`), '');
writeFileSync(join(results, 'summary.md'), out.join('\n'));
process.exit(invalid.length ? 1 : 0);
