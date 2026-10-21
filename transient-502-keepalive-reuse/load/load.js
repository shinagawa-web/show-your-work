const http = require('node:http');
const fs = require('node:fs');

const env = (k, d) => (process.env[k] === undefined || process.env[k] === '' ? d : Number(process.env[k]));
const durationS = env('DURATION_S', 60);
const users = env('USERS', 16);
const thinkMs = env('THINK_MS', 10);
const burstSize = env('BURST_SIZE', 0);
const burstMinS = env('BURST_MIN_S', 0);
const burstMaxS = env('BURST_MAX_S', 0);
let seed = env('SEED', 1) >>> 0;
const out = fs.openSync(process.env.OUT, 'w');
const burstOut = fs.openSync(process.env.BURST_OUT, 'w');

const now = () => (performance.timeOrigin + performance.now()) / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const random = () => {
  seed = (seed + 0x6d2b79f5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

let seq = 0;
const lines = [];
function one(agent, kind) {
  const id = ++seq;
  const method = id % 2 ? 'GET' : 'POST';
  const t0 = now();
  return new Promise((resolve) => {
    const done = (status, err) => {
      lines.push(`${t0.toFixed(6)} ${now().toFixed(6)} ${kind} ${id} ${method} ${status} ${err}\n`);
      resolve();
    };
    const req = http.request(
      { host: '127.0.0.1', port: 8080, method, path: `/?id=${id}`, agent,
        headers: method === 'POST' ? { 'content-type': 'application/x-www-form-urlencoded', 'content-length': 3 } : {} },
      (res) => {
        res.resume();
        res.on('end', () => done(res.statusCode, '-'));
        res.on('error', (e) => done(res.statusCode, e.code || e.message));
      });
    req.setTimeout(10000, () => req.destroy(new Error('timeout')));
    req.on('error', (e) => done(0, e.code || e.message.replace(/ /g, '_')));
    req.end(method === 'POST' ? 'x=1' : undefined);
  });
}

async function steady() {
  const agent = new http.Agent({ keepAlive: true, maxSockets: users });
  await Promise.all(Array.from({ length: users }, async () => {
    while (now() < end) {
      await one(agent, 'steady');
      await sleep(thinkMs);
    }
  }));
}

async function bursts() {
  const agent = new http.Agent({ keepAlive: true, maxSockets: burstSize });
  let prev = null;
  for (;;) {
    const gap = burstMinS + random() * (burstMaxS - burstMinS);
    const at = (prev === null ? now() + 2 : prev + gap);
    if (at >= end) break;
    await sleep((at - now()) * 1000);
    if (now() >= end) break;
    const t = now();
    fs.writeSync(burstOut, `${t.toFixed(6)} ${prev === null ? '-' : (t - prev).toFixed(6)}\n`);
    prev = t;
    await Promise.all(Array.from({ length: burstSize }, () => one(agent, 'burst')));
  }
}

let end = now() + durationS;
process.on('SIGUSR2', () => { end = now(); });

(async () => {
  const jobs = [steady()];
  if (burstSize > 0) jobs.push(bursts());
  await Promise.all(jobs);
  fs.writeSync(out, lines.join(''));
  process.exit(0);
})();
