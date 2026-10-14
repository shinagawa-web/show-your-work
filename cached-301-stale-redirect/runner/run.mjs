import { chromium, firefox, webkit } from 'playwright';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs/promises';
import fss from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { CONDITIONS, CURL_URLS, LINKS_URL, WAIT_AFTER_SWITCH_SEC, IMAGE, CONTAINER } from './plan.mjs';

const exec = promisify(execFile);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const argv = process.argv.slice(2);
const arg = (name, def) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
};
const OUT = path.resolve(arg('out', path.join(import.meta.dirname, '..', 'results', 'local')));
const BROWSERS = arg('browsers', 'chromium,webkit,firefox').split(',');
const runId = new Date().toISOString().replace(/[:.]/g, '-');
const PROFILES = path.join(os.tmpdir(), 'cached-301-profiles', runId);

const TYPES = {
  chromium: { type: chromium, opts: { channel: 'chromium' } },
  webkit: { type: webkit, opts: {} },
  firefox: { type: firefox, opts: {} },
};
for (const b of BROWSERS) if (!TYPES[b]) throw new Error(`unknown browser ${b}`);

const log = (...a) => console.error(`[${new Date().toISOString()}]`, ...a);

async function docker(...a) {
  return exec('docker', a, { maxBuffer: 64 * 1024 * 1024 });
}

async function curlHeaders(url) {
  const { stdout } = await exec('curl', ['-sS', '-4', '-D', '-', '-o', '/dev/null', url]);
  return stdout;
}

async function curlAll() {
  const out = {};
  for (const u of CURL_URLS) out[u] = { at: Date.now(), raw: await curlHeaders(u) };
  return out;
}

const locationOf = (raw) => (raw.match(/^location:\s*(\S+)/im) || [])[1] || null;

async function waitReady() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(LINKS_URL);
      if (r.status === 200) return;
    } catch {}
    await sleep(100);
  }
  throw new Error('nginx not ready');
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function observePage(page) {
  const o = { requests: [], responses: [] };
  page.on('request', (r) => {
    if (r.isNavigationRequest() && r.frame() === page.mainFrame()) {
      o.requests.push({ t: Date.now(), url: r.url(), redirectedFrom: r.redirectedFrom()?.url() ?? null });
    }
  });
  page.on('response', (r) => {
    const q = r.request();
    if (q.isNavigationRequest() && q.frame() === page.mainFrame()) {
      o.responses.push({ t: Date.now(), url: r.url(), status: r.status(), headers: r.headers() });
    }
  });
  return o;
}

async function attachCdp(ctx, page) {
  const session = await ctx.newCDPSession(page);
  const events = [];
  const pick = (r) => r && {
    url: r.url,
    status: r.status,
    fromDiskCache: r.fromDiskCache ?? null,
    fromPrefetchCache: r.fromPrefetchCache ?? null,
    fromServiceWorker: r.fromServiceWorker ?? null,
    headers: r.headers,
  };
  session.on('Network.requestWillBeSent', (e) => events.push({
    t: Date.now(), event: 'requestWillBeSent', requestId: e.requestId, url: e.request.url, type: e.type ?? null,
    redirectResponse: pick(e.redirectResponse),
  }));
  session.on('Network.responseReceived', (e) => events.push({
    t: Date.now(), event: 'responseReceived', requestId: e.requestId, type: e.type ?? null, response: pick(e.response),
  }));
  session.on('Network.responseReceivedExtraInfo', (e) => events.push({
    t: Date.now(), event: 'responseReceivedExtraInfo', requestId: e.requestId, statusCode: e.statusCode ?? null, headers: e.headers,
  }));
  await session.send('Network.enable');
  return { session, events };
}

async function openEntrance(browser, ctx, cond, step, how, clock) {
  const rec = { browser, condition: cond.id, step, how, entrance: cond.entrance };
  const page = await ctx.newPage();
  const cdp = browser === 'chromium' ? await attachCdp(ctx, page) : null;
  let obs = null;
  try {
    if (how === 'link') {
      await page.goto(LINKS_URL, { waitUntil: 'load' });
      if (cdp) cdp.events.length = 0;
      obs = observePage(page);
      rec.startedAt = Date.now();
      await Promise.all([
        page.waitForURL(/\/docs\/v\d+\/$/, { waitUntil: 'load', timeout: 15000 }),
        page.click(`#${cond.id}`),
      ]);
    } else {
      obs = observePage(page);
      rec.startedAt = Date.now();
      await page.goto(cond.entrance, { waitUntil: 'load', timeout: 15000 });
    }
    await sleep(100);
    rec.endedAt = Date.now();
    rec.finalUrl = page.url();
    rec.version = (await page.textContent('#version'))?.trim() ?? null;
    rec.userAgent = await page.evaluate(() => navigator.userAgent);
  } catch (e) {
    rec.endedAt = Date.now();
    rec.error = String(e?.message || e);
  }
  rec.secSinceSwitch = clock.switchDone ? (rec.startedAt - clock.switchDone) / 1000 : null;
  rec.playwright = obs;
  if (cdp) {
    rec.cdp = cdp.events;
    const hop = cdp.events.find((e) => e.event === 'requestWillBeSent' && e.redirectResponse?.url === cond.entrance);
    rec.entranceFromDiskCache = hop ? hop.redirectResponse.fromDiskCache : null;
    rec.entranceStatusSeen = hop ? hop.redirectResponse.status : null;
    await cdp.session.detach().catch(() => {});
  }
  await page.close();
  log(`${browser} ${cond.id} ${step} ${how} -> ${rec.version ?? rec.error}`);
  return rec;
}

async function openClearSiteDataPage(browser, ctx, cond, clock) {
  const rec = { browser, condition: cond.id, step: 'clear-site-data-page', how: 'goto', url: cond.clearSiteDataPage };
  const page = await ctx.newPage();
  const obs = observePage(page);
  const cdp = browser === 'chromium' ? await attachCdp(ctx, page) : null;
  try {
    rec.startedAt = Date.now();
    const resp = await page.goto(cond.clearSiteDataPage, { waitUntil: 'load', timeout: 15000 });
    rec.status = resp?.status() ?? null;
    rec.headers = resp ? resp.headers() : null;
    await sleep(500);
    rec.endedAt = Date.now();
    rec.version = (await page.textContent('#version'))?.trim() ?? null;
  } catch (e) {
    rec.endedAt = Date.now();
    rec.error = String(e?.message || e);
  }
  rec.secSinceSwitch = (rec.startedAt - clock.switchDone) / 1000;
  rec.playwright = obs;
  if (cdp) {
    rec.cdp = cdp.events;
    await cdp.session.detach().catch(() => {});
  }
  await page.close();
  log(`${browser} ${cond.id} clear-site-data-page -> ${rec.version ?? rec.error}`);
  return rec;
}

async function runBrowser(name, gates, clock, record) {
  const { type, opts } = TYPES[name];
  const profiles = [...new Set(CONDITIONS.map((c) => c.profile))];
  const launch = (p) => type.launchPersistentContext(path.join(PROFILES, name, p), { headless: true, ...opts });
  let ctxs = Object.fromEntries(await Promise.all(profiles.map(async (p) => [p, await launch(p)])));

  for (const c of CONDITIONS) record.push(await openEntrance(name, ctxs[c.profile], c, 'first', 'link', clock));
  gates.firstDone[name].resolve();

  await gates.switched.promise;
  for (const c of CONDITIONS) {
    if (c.clearSiteDataPage) {
      record.push(await openEntrance(name, ctxs[c.profile], c, 'before-clear-site-data', c.afterSwitch, clock));
      record.push(await openClearSiteDataPage(name, ctxs[c.profile], c, clock));
    }
    record.push(await openEntrance(name, ctxs[c.profile], c, 'immediate', c.afterSwitch, clock));
  }

  await gates.afterWait.promise;
  for (const c of CONDITIONS) record.push(await openEntrance(name, ctxs[c.profile], c, 'after-wait', c.afterSwitch, clock));

  for (const ctx of Object.values(ctxs)) await ctx.close();
  clock.restart[name] = Date.now();
  ctxs = Object.fromEntries(await Promise.all(profiles.map(async (p) => [p, await launch(p)])));
  for (const c of CONDITIONS) record.push(await openEntrance(name, ctxs[c.profile], c, 'restart', c.afterSwitch, clock));
  for (const ctx of Object.values(ctxs)) await ctx.close();
}

const LOG_LINE = /^(\d+\.\d+) pid=\d+ (\d+) "(\S+) (\S+) [^"]*" (\d{3}) loc="([^"]*)" cc="([^"]*)" "([^"]*)"$/;
function parseAccessLog(text) {
  return text.split('\n').map((l) => {
    const m = l.match(LOG_LINE);
    if (!m) return null;
    return { t: Math.round(Number(m[1]) * 1000), port: m[2], method: m[3], path: m[4], status: Number(m[5]), location: m[6], cacheControl: m[7], userAgent: m[8], raw: l };
  }).filter(Boolean);
}
const browserOfUa = (ua) => (/Firefox\//.test(ua) ? 'firefox' : /Chrome\//.test(ua) ? 'chromium' : /AppleWebKit\//.test(ua) ? 'webkit' : 'other');

async function main() {
  await fs.mkdir(OUT, { recursive: true });
  const timings = { start: Date.now() };

  const env = {
    runId,
    node: process.version,
    playwright: JSON.parse(fss.readFileSync(path.join(import.meta.dirname, 'node_modules/playwright/package.json'))).version,
    nproc: os.cpus().length,
    totalMemMB: Math.round(os.totalmem() / 1048576),
    kernel: os.release(),
    browsers: {},
  };
  for (const b of BROWSERS) {
    const { type, opts } = TYPES[b];
    const br = await type.launch({ headless: true, ...opts });
    env.browsers[b] = { version: br.version(), executablePath: type.executablePath(), channel: opts.channel ?? null };
    await br.close();
  }
  env.image = (await docker('image', 'inspect', '--format', '{{.Id}}', IMAGE)).stdout.trim();
  env.nginx = (await docker('run', '--rm', '--entrypoint', 'nginx', IMAGE, '-v')).stderr.trim();

  await docker('rm', '-f', CONTAINER).catch(() => {});
  await docker('run', '-d', '--name', CONTAINER, '--network', 'host', '-e', 'TARGET=v3', IMAGE);
  await waitReady();
  timings.serverReady = Date.now();

  const nginxMainConf = (await docker('exec', CONTAINER, 'cat', '/etc/nginx/nginx.conf')).stdout;
  const nginxConf = { v3: (await docker('exec', CONTAINER, 'cat', '/etc/nginx/conf.d/docs.conf')).stdout };
  const curl = { v3: await curlAll() };
  let valid = true;
  const invalid = [];
  for (const c of CONDITIONS) {
    if (!locationOf(curl.v3[c.entrance].raw)?.endsWith('/docs/v3/')) { valid = false; invalid.push(`curl v3 ${c.id}`); }
  }

  const clock = { switchDone: null, restart: {} };
  const gates = {
    firstDone: Object.fromEntries(BROWSERS.map((b) => [b, deferred()])),
    switched: deferred(),
    afterWait: deferred(),
  };
  const record = [];
  const runs = BROWSERS.map((b) => runBrowser(b, gates, clock, record).catch((e) => {
    log(`${b} failed`, e);
    valid = false;
    invalid.push(`${b} run failed: ${e?.message || e}`);
    for (const g of [gates.firstDone[b]]) g.resolve();
  }));

  await Promise.all(BROWSERS.map((b) => gates.firstDone[b].promise));
  timings.firstDone = Date.now();

  const workerPids = async () => (await docker('exec', CONTAINER, 'sh', '-c',
    'for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in "nginx: worker"*) echo ${p#/proc/};; esac; done',
  )).stdout.split(/\s+/).filter(Boolean);
  const oldWorkers = await workerPids();
  timings.switchStart = Date.now();
  const sw = await docker('exec', CONTAINER, 'set-target', 'v4');
  const switchLog = sw.stdout + sw.stderr;
  const drain = [];
  for (let i = 0; i < 100; i++) {
    const now = await workerPids();
    const remaining = now.filter((p) => oldWorkers.includes(p));
    drain.push({ t: Date.now(), workers: now, oldRemaining: remaining });
    if (remaining.length === 0 && now.length > 0) break;
    await sleep(100);
  }
  for (let i = 0; i < 50; i++) {
    const ok = await Promise.all(CONDITIONS.map(async (c) => locationOf(await curlHeaders(c.entrance))?.endsWith('/docs/v4/')));
    if (ok.every(Boolean)) break;
    await sleep(100);
  }
  clock.switchDone = Date.now();
  if (drain.at(-1).oldRemaining.length !== 0) { valid = false; invalid.push('old nginx workers still running after the switch'); }
  timings.switchDone = clock.switchDone;
  nginxConf.v4 = (await docker('exec', CONTAINER, 'cat', '/etc/nginx/conf.d/docs.conf')).stdout;
  curl.v4 = await curlAll();
  for (const c of CONDITIONS) {
    if (!locationOf(curl.v4[c.entrance].raw)?.endsWith('/docs/v4/')) { valid = false; invalid.push(`curl v4 ${c.id}`); }
  }
  gates.switched.resolve();
  setTimeout(() => gates.afterWait.resolve(), clock.switchDone + WAIT_AFTER_SWITCH_SEC * 1000 - Date.now());

  await Promise.all(runs);
  timings.end = Date.now();

  const logs = await docker('logs', CONTAINER);
  await docker('rm', '-f', CONTAINER);
  const access = parseAccessLog(logs.stdout);

  for (const r of record) {
    if (r.startedAt == null) continue;
    const entrancePath = new URL(r.entrance ?? r.url).pathname;
    const port = new URL(r.entrance ?? r.url).port;
    r.nginx = access.filter((a) => a.path === entrancePath && a.port === port && browserOfUa(a.userAgent) === r.browser
      && a.t >= r.startedAt - 50 && a.t <= r.endedAt + 50).map((a) => a.raw);
    r.reachedNginx = r.nginx.length > 0;
  }

  for (const r of record) {
    if (r.error) { valid = false; invalid.push(`${r.browser} ${r.condition} ${r.step}: ${r.error}`); }
    if (r.step === 'first' && (r.version !== 'v3' || !r.reachedNginx)) { valid = false; invalid.push(`${r.browser} ${r.condition} first: version=${r.version} reachedNginx=${r.reachedNginx}`); }
    if (r.step === 'after-wait' && r.secSinceSwitch < WAIT_AFTER_SWITCH_SEC) { valid = false; invalid.push(`${r.browser} ${r.condition} after-wait started ${r.secSinceSwitch}s after switch`); }
  }
  for (const a of access) {
    if (a.t > clock.switchDone && a.location.endsWith('/docs/v3/')) { valid = false; invalid.push(`served v3 after the switch: ${a.raw}`); }
  }
  const unattributed = access.filter((a) => browserOfUa(a.userAgent) === 'other' && !a.userAgent.startsWith('curl/'));

  const out = { env, timings, valid, invalid, waitAfterSwitchSec: WAIT_AFTER_SWITCH_SEC, conditions: CONDITIONS, nginxConf, nginxMainConf, switchLog, oldWorkers, drain, curl, clock, opens: record, unattributedAccess: unattributed.map((a) => a.raw) };
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify(out, null, 1));
  await fs.writeFile(path.join(OUT, 'nginx-access.log'), logs.stdout);
  await fs.writeFile(path.join(OUT, 'nginx-error.log'), logs.stderr);
  await fs.rm(PROFILES, { recursive: true, force: true });
  log(`valid=${valid}`, invalid.join('; '));
  if (!valid) process.exit(2);
}

main().catch((e) => { console.error(e); process.exit(1); });
