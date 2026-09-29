// Runs the plan: start containers, fetch v1, deploy v2, revisit, record.
//
//   node run.mjs [--sets cal-wait,cal-age,deploytime,grid,keep,spa,nocache,nocache304,maxage,maxage300,reloadfail,pressfirst]
//                [--ages 10m,100m,1000m,6d] [--parallel 4] [--max-browsers 16]
//                [--out ../results/<run-id>] [--no-calibration-gate]
//
// Images site-repro:<name> (see plan.mjs IMAGES) must already be built.
// No request interception is used; cache behaviour is read from
// Navigation/Resource Timing and the nginx access log.

import { chromium } from 'playwright';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs/promises';
import fss from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildPlan, LATE_DEPLOY_LEAD_SEC } from './plan.mjs';

const exec = promisify(execFile);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- args ----------
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const ALL_SETS = ['cal-wait', 'cal-age', 'deploytime', 'grid', 'keep', 'spa', 'nocache', 'nocache304', 'maxage', 'maxage300', 'reloadfail', 'pressfirst'];
// Sets that wait in real time. They run first, all at once (they mostly sleep).
// A set is real-wait per group: a group with realWaitSec users (e.g. the
// pressfirst-wait group) runs in this phase too.
const REAL_WAIT_SETS = ['cal-wait', 'cal-age', 'deploytime'];
const isRealWait = (g) => REAL_WAIT_SETS.includes(g.set) || g.users.some((u) => u.realWaitSec != null);
const sets = args.sets ? String(args.sets).split(',') : ALL_SETS;
const ages = args.ages ? String(args.ages).split(',') : null;
const PARALLEL = Number(args.parallel || 4);
const MAX_BROWSERS = Number(args['max-browsers'] || 16);
const runId = new Date().toISOString().replace(/[:.]/g, '-');
const OUT = path.resolve(args.out || path.join(import.meta.dirname, '..', 'results', runId));
const PROFILES = path.join(os.tmpdir(), 'clhf-profiles', runId);
const IMAGE_PREFIX = 'site-repro:';
const BASE_PORT = Number(args['base-port'] || 18000);

for (const s of sets) if (!ALL_SETS.includes(s)) throw new Error(`unknown set ${s}`);

// ---------- load sampling ----------
let activeBrowsers = 0;
let peakBrowsers = 0;
const loadSamples = [];
let lastStat = null;
function cpuUtil() {
  try {
    const line = fss.readFileSync('/proc/stat', 'utf8').split('\n')[0].trim().split(/\s+/).slice(1).map(Number);
    const idle = line[3] + line[4];
    const total = line.reduce((a, b) => a + b, 0);
    let util = null;
    if (lastStat) util = 1 - (idle - lastStat.idle) / (total - lastStat.total);
    lastStat = { idle, total };
    return util;
  } catch { return null; }
}
const sampler = setInterval(() => {
  loadSamples.push({ t: Date.now(), loadavg1: os.loadavg()[0], cpuUtil: cpuUtil(), activeBrowsers });
}, 1000);
const loadNow = () => ({ activeBrowsers, loadavg1: os.loadavg()[0] });

// ---------- semaphores ----------
function semaphore(n) {
  let free = n;
  const q = [];
  return async (fn) => {
    if (free === 0) await new Promise((r) => q.push(r));
    free--;
    try { return await fn(); } finally { free++; q.shift()?.(); }
  };
}
const browserSlot = semaphore(MAX_BROWSERS);
const groupSlot = semaphore(PARALLEL);

// ---------- docker ----------
async function docker(...a) {
  const { stdout, stderr } = await exec('docker', a, { maxBuffer: 64 * 1024 * 1024 });
  return { stdout, stderr };
}
async function waitReady(port) {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/50x.html`);
      if (r.status > 0) return;
    } catch { /* not yet */ }
    await sleep(100);
  }
  throw new Error(`port ${port} not ready`);
}
async function startContainer(name, port, spec, extraEnv) {
  const env = { ...(spec.env || {}), ...extraEnv };
  const envArgs = Object.entries(env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
  const t0 = Date.now();
  await docker('run', '-d', '--name', name, '-p', `127.0.0.1:${port}:80`, ...envArgs, IMAGE_PREFIX + spec.image);
  await waitReady(port);
  return { name, image: IMAGE_PREFIX + spec.image, env, startedAt: t0, readyAt: Date.now() };
}
async function stopContainer(name) {
  await docker('stop', '-t', '1', name);
  const logs = await docker('logs', name);
  await docker('rm', name);
  return { accessLog: logs.stdout, errorLog: logs.stderr };
}

// ---------- browser ----------
const UA = (id) => `Mozilla/5.0 (X11; Linux) chunk-repro/${id}`;

async function withContext(user, fn) {
  return browserSlot(async () => {
    activeBrowsers++;
    peakBrowsers = Math.max(peakBrowsers, activeBrowsers);
    const ctx = await chromium.launchPersistentContext(path.join(PROFILES, user.id), {
      headless: true,
      userAgent: UA(user.id),
    });
    try { return await fn(ctx); } finally {
      await ctx.close();
      activeBrowsers--;
    }
  });
}

function observe(page) {
  const o = { console: [], pageErrors: [], requestFailed: [], responses: [] };
  page.on('console', (m) => o.console.push({ t: Date.now(), type: m.type(), text: m.text(), url: m.location()?.url }));
  page.on('pageerror', (e) => o.pageErrors.push({ t: Date.now(), name: e.name, message: e.message }));
  page.on('requestfailed', (r) => o.requestFailed.push({ t: Date.now(), url: r.url(), failure: r.failure()?.errorText }));
  page.on('response', (r) => o.responses.push({ t: Date.now(), url: r.url(), status: r.status() }));
  return o;
}

async function pageState(page) {
  return page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    return {
      timeOrigin: performance.timeOrigin,
      nav: nav && {
        type: nav.type,
        transferSize: nav.transferSize,
        encodedBodySize: nav.encodedBodySize,
        responseStatus: nav.responseStatus,
        responseStartAbs: performance.timeOrigin + nav.responseStart,
      },
      resources: performance.getEntriesByType('resource').map((r) => ({
        name: r.name, transferSize: r.transferSize, encodedBodySize: r.encodedBodySize, responseStatus: r.responseStatus,
      })),
      app: window.__app ? {
        version: window.__app.version, loaded: window.__app.loaded, errors: [...window.__app.errors], log: window.__app.log,
      } : null,
      scripts: [...document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src')),
    };
  });
}

function headersOf(resp) {
  if (!resp) return null;
  const h = resp.headers();
  return {
    status: resp.status(),
    date: h['date'] ?? null,
    lastModified: h['last-modified'] ?? null,
    age: h['age'] ?? null,
    cacheControl: h['cache-control'] ?? null,
    etag: h['etag'] ?? null,
  };
}

// Click the button and wait for the chunk to load or fail. If the page
// reloads itself (reload-on-failure countermeasure), observe the new
// document and click once more.
async function clickAndObserve(page) {
  const attempts = [];
  for (let attempt = 1; attempt <= 2; attempt++) {
    let navs = 0;
    const onNav = (f) => { if (f === page.mainFrame()) navs++; };
    page.on('framenavigated', onNav);
    const clickedAt = Date.now();
    await page.click('#open');
    let state = null;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      await sleep(100);
      if (navs > 0) break;
      try {
        state = await pageState(page);
        if (state.app && (state.app.loaded || state.app.errors.length)) break;
      } catch { /* navigating */ }
    }
    // Give a reload-on-failure handler time to navigate.
    const settle = Date.now() + 1500;
    while (navs === 0 && Date.now() < settle) await sleep(100);
    page.off('framenavigated', onNav);
    if (navs > 0) {
      await page.waitForLoadState('load');
      await sleep(200);
      const after = await pageState(page);
      attempts.push({ attempt, clickedAt, result: state && summarizeApp(state), reloaded: true, afterReload: after });
      continue;
    }
    attempts.push({ attempt, clickedAt, result: state && summarizeApp(state), reloaded: false });
    break;
  }
  return attempts;
}
const summarizeApp = (s) => s.app && { version: s.app.version, loaded: s.app.loaded, errors: s.app.errors };

// ---------- one group ----------
async function runGroup(g, port, record) {
  const v1Name = `clhf-${runId}-${g.id}-v1`.toLowerCase();
  const v2Name = `clhf-${runId}-${g.id}-v2`.toLowerCase();
  const gr = { id: g.id, set: g.set, port, ageSec: g.ageSec, ageHeader: g.ageHeader ?? null, timeline: {} };
  const v1Env = { AGE_SEC: String(g.ageSec), ...(g.ageHeader != null ? { AGE_HEADER: String(g.ageHeader) } : {}) };
  gr.v1 = await startContainer(v1Name, port, g.v1, v1Env);

  // 1. every user fetches v1 and closes the browser (cache stays on disk)
  const results = await Promise.all(g.users.map((u) => withContext(u, async (ctx) => {
    const page = await ctx.newPage();
    const o = observe(page);
    const startedAt = Date.now();
    const resp = await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'load' });
    await sleep(100);
    const st = await pageState(page);
    // pressFirst: press the button on the first visit too, and wait for the
    // chunk to load before the browser is closed.
    let click = null;
    let afterClick = null;
    if (g.pressFirst) {
      click = await clickAndObserve(page);
      afterClick = await pageState(page);
    }
    await page.close();
    return { user: u, fetch: { startedAt, response: headersOf(resp), state: st, click, afterClick, ...o, load: loadNow() } };
  })));
  gr.timeline.fetchesDone = Date.now();

  // 2. deploy: replace the v1 container with v2 on the same port.
  // deployAt (real-wait groups only): early = now, mid = half of the earliest
  // r of the group, late = LATE_DEPLOY_LEAD_SEC before the earliest revisit.
  // deploy === false: no deploy, v1 keeps serving the revisit.
  gr.deployAt = g.deploy === false ? 'none' : (g.deployAt || 'early');
  if (g.deployAt === 'mid' || g.deployAt === 'late') {
    const due = Math.min(...results.map((r) => r.fetch.state.nav.responseStartAbs + r.user.realWaitSec * 1000));
    const fetched = Math.max(...results.map((r) => r.fetch.state.nav.responseStartAbs));
    const at = g.deployAt === 'mid'
      ? Math.min(...results.map((r) => r.fetch.state.nav.responseStartAbs + (r.user.realWaitSec * 1000) / 2))
      : due - LATE_DEPLOY_LEAD_SEC * 1000;
    gr.timeline.deployPlannedAt = at;
    if (at < fetched) throw new Error(`${g.id}: planned deploy before last fetch`);
    const wait = at - Date.now();
    if (wait > 0) await sleep(wait);
  }
  if (g.deploy === false) {
    gr.v1.logsAfterFetch = (await docker('logs', v1Name)).stdout;
  } else {
    gr.timeline.deployStart = Date.now();
    gr.v1.logs = await stopContainer(v1Name);
    gr.v2 = await startContainer(v2Name, port, g.v2, { AGE_SEC: '0' });
    gr.timeline.deployDone = Date.now();
  }

  // 3. revisit: relaunch the same profile, open a new tab, press the button
  await Promise.all(results.map(async (r) => {
    const u = r.user;
    if (u.realWaitSec != null) {
      const due = r.fetch.state.nav.responseStartAbs + u.realWaitSec * 1000;
      const wait = due - Date.now();
      if (wait > 0) await sleep(wait);
    }
    r.revisit = await withContext(u, async (ctx) => {
      const page = await ctx.newPage();
      const o = observe(page);
      const rv = {
        newTabHow: 'browser closed after fetch; relaunched persistent context on the same profile; context.newPage(); page.goto(url)',
        load: loadNow(),
      };
      const resp = await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'load' });
      await sleep(100);
      rv.response = headersOf(resp);
      rv.state = await pageState(page);
      if (u.method === 'reload') {
        const resp2 = await page.reload({ waitUntil: 'load' });
        await sleep(100);
        rv.afterReload = { response: headersOf(resp2), state: await pageState(page) };
      }
      rv.click = await clickAndObserve(page);
      rv.finalState = await pageState(page);
      Object.assign(rv, o);
      return rv;
    });
  }));
  gr.timeline.revisitsDone = Date.now();
  if (g.deploy === false) {
    // Same container served fetch and revisit: split its log at the snapshot.
    const all = await stopContainer(v1Name);
    gr.v1.logs = { accessLog: gr.v1.logsAfterFetch, errorLog: all.errorLog };
    gr.v2 = { image: gr.v1.image, env: gr.v1.env, note: 'no deploy; revisit served by the v1 container',
      logs: { accessLog: all.accessLog.slice(gr.v1.logsAfterFetch.length), errorLog: '' } };
    delete gr.v1.logsAfterFetch;
  } else {
    gr.v2.logs = await stopContainer(v2Name);
  }

  for (const r of results) {
    // log_format main of the nginx image: ... "$http_user_agent" "$http_x_forwarded_for"
    const tag = `chunk-repro/${r.user.id}"`;
    const pick = (log) => log.split('\n').filter((l) => l.includes(tag));
    const fetchHdr = r.fetch.response;
    const aSec = fetchHdr?.date && fetchHdr?.lastModified
      ? (Date.parse(fetchHdr.date) - Date.parse(fetchHdr.lastModified)) / 1000 : null;
    const elapsedSec = (r.revisit.state.timeOrigin - r.fetch.state.nav.responseStartAbs) / 1000;
    const rSec = (g.ageHeader ?? 0) + elapsedSec;
    record.push({
      user: r.user.id,
      group: g.id,
      set: g.set,
      method: r.user.method,
      point: r.user.point,
      repeat: r.user.repeat,
      rSource: r.user.realWaitSec != null ? 'real-wait' : 'age-header',
      target: { ageSec: g.ageSec, ageHeader: g.ageHeader ?? null, realWaitSec: r.user.realWaitSec ?? null },
      measured: {
        A_sec: aSec,
        elapsedSec,
        r_sec: rSec,
        r_over_A: aSec ? rSec / aSec : null,
        deployDoneBeforeRevisit: gr.timeline.deployDone ? gr.timeline.deployDone < r.revisit.state.timeOrigin : null,
        // position of the deploy between fetch (0) and revisit (1)
        deployStartFrac: gr.timeline.deployStart
          ? (gr.timeline.deployStart - r.fetch.state.nav.responseStartAbs) / (r.revisit.state.timeOrigin - r.fetch.state.nav.responseStartAbs) : null,
        deployDoneFrac: gr.timeline.deployDone
          ? (gr.timeline.deployDone - r.fetch.state.nav.responseStartAbs) / (r.revisit.state.timeOrigin - r.fetch.state.nav.responseStartAbs) : null,
      },
      deployAt: gr.deployAt,
      bootedVersion: r.revisit.state.app?.version ?? null,
      navTransferSize: r.revisit.state.nav?.transferSize ?? null,
      chunk: r.revisit.click,
      // Resource Timing entries of the chunk (feature-*.js). transferSize 0 = served from cache.
      firstVisitChunk: g.pressFirst ? {
        result: r.fetch.click,
        resources: chunkResources(r.fetch.afterClick),
      } : null,
      revisitChunkResources: chunkResources(r.revisit.finalState),
      fetch: r.fetch,
      revisit: r.revisit,
      accessLog: { v1: pick(gr.v1.logs.accessLog), v2: pick(gr.v2.logs.accessLog) },
      container: { port, v1: gr.v1.image, v2: gr.v2.image, v1Env: gr.v1.env, v2Env: gr.v2.env },
    });
  }
  return gr;
}

const chunkResources = (st) => (st?.resources || []).filter((x) => /\/assets\/feature-[^/]*\.js$/.test(x.name));

async function runGroups(groups, portOf, slot = groupSlot) {
  const record = [];
  const groupRecords = [];
  await Promise.all(groups.map((g) => slot(async () => {
    const t0 = Date.now();
    const gr = await runGroup(g, portOf(g), record);
    gr.timeline.start = t0;
    groupRecords.push(gr);
    console.error(`[${new Date().toISOString()}] done ${g.id} in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  })));
  return { record, groupRecords };
}

// Calibration gate. Each cal-wait / cal-age user is judged by its measured
// r/A (not by its point label): v1 is expected when r/A < CAL_BOUNDARY, v2
// when r/A >= CAL_BOUNDARY. A user fails when the booted version differs
// from the expected one, or when r/A or the booted version is missing.
const CAL_BOUNDARY = 0.1;
function calibrationCheck(record) {
  const rows = record
    .filter((r) => ['cal-wait', 'cal-age'].includes(r.set))
    .map((r) => {
      const rOverA = r.measured.r_over_A;
      const expected = rOverA == null ? null : (rOverA < CAL_BOUNDARY ? 'v1' : 'v2');
      return {
        user: r.user,
        set: r.set,
        method: r.rSource,
        point: r.point,
        A_sec: r.measured.A_sec,
        r_sec: r.measured.r_sec,
        r_over_A: rOverA,
        expected,
        booted: r.bootedVersion,
        pass: expected != null && r.bootedVersion === expected,
      };
    })
    .sort((a, b) => a.method.localeCompare(b.method) || a.r_over_A - b.r_over_A);
  return { rule: `expected v1 if measured r/A < ${CAL_BOUNDARY}, else v2`, boundary: CAL_BOUNDARY, rows, pass: rows.every((r) => r.pass) };
}

// ---------- main ----------
async function main() {
  await fs.mkdir(OUT, { recursive: true });
  const timings = { start: Date.now() };
  let groups = buildPlan().filter((g) => sets.includes(g.set));
  if (ages) groups = groups.filter((g) => !['grid', 'pressfirst'].includes(g.set) || ages.includes(g.ageName));
  const ports = new Map(groups.map((g, i) => [g.id, BASE_PORT + i]));
  const portOf = (g) => ports.get(g.id);

  const probe = await chromium.launch({ headless: true });
  const env = {
    runId,
    node: process.version,
    playwright: JSON.parse(fss.readFileSync(path.join(import.meta.dirname, 'node_modules/playwright/package.json'))).version,
    browserVersion: probe.version(),
    executablePath: chromium.executablePath(),
    nproc: os.cpus().length,
    totalMemMB: Math.round(os.totalmem() / 1048576),
    kernel: os.release(),
    parallel: PARALLEL,
    maxBrowsers: MAX_BROWSERS,
    sets,
    ages,
    images: {},
  };
  await probe.close();
  for (const img of new Set(groups.flatMap((g) => [g.v1.image, g.v2.image]))) {
    const { stdout } = await docker('image', 'inspect', '--format', '{{.Id}}', IMAGE_PREFIX + img);
    env.images[img] = stdout.trim();
  }
  env.nginx = (await docker('run', '--rm', '--entrypoint', 'nginx', IMAGE_PREFIX + 'v1', '-v')).stderr.trim();

  const record = [];
  const groupRecords = [];
  let calibration = null;

  const calGroups = groups.filter(isRealWait);
  const mainGroups = groups.filter((g) => !isRealWait(g));
  if (calGroups.length) {
    timings.calibrationStart = Date.now();
    const res = await runGroups(calGroups, portOf, (fn) => fn());
    record.push(...res.record);
    groupRecords.push(...res.groupRecords);
    timings.calibrationEnd = Date.now();
    if (sets.includes('cal-wait') && sets.includes('cal-age')) {
      calibration = calibrationCheck(res.record);
      for (const r of calibration.rows) {
        console.error(`calibration ${r.user} ${r.method} r/A=${r.r_over_A?.toFixed(4)} expected=${r.expected} booted=${r.booted} ${r.pass ? 'pass' : 'FAIL'}`);
      }
      console.error(`calibration: ${calibration.pass ? 'pass' : 'FAIL'}`);
    }
  }
  let stopped = false;
  if (calibration && !calibration.pass && !args['no-calibration-gate']) {
    stopped = true;
  } else if (mainGroups.length) {
    timings.mainStart = Date.now();
    const res = await runGroups(mainGroups, portOf);
    record.push(...res.record);
    groupRecords.push(...res.groupRecords);
    timings.mainEnd = Date.now();
  }
  timings.end = Date.now();
  clearInterval(sampler);

  record.sort((a, b) => a.user.localeCompare(b.user));
  const out = {
    env, timings, calibration, stoppedAfterCalibration: stopped, peakBrowsers,
    loadSamples, groups: groupRecords.map((g) => ({ ...g, v1: { ...g.v1, logs: undefined }, v2: g.v2 && { ...g.v2, logs: undefined } })),
    users: record,
  };
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify(out, null, 1));
  const logDir = path.join(OUT, 'nginx');
  await fs.mkdir(logDir, { recursive: true });
  for (const g of groupRecords) {
    for (const v of ['v1', 'v2']) {
      if (!g[v]?.logs) continue;
      await fs.writeFile(path.join(logDir, `${g.id}-${v}.access.log`), g[v].logs.accessLog);
      await fs.writeFile(path.join(logDir, `${g.id}-${v}.error.log`), g[v].logs.errorLog);
    }
  }
  console.error(`wrote ${OUT}`);
  await fs.rm(PROFILES, { recursive: true, force: true });
  if (stopped) process.exit(2);
}

main().catch((e) => { console.error(e); process.exit(1); });
