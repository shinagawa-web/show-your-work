import http from 'node:http';
import { monitorEventLoopDelay } from 'node:perf_hooks';

const WORK_MS = Number(process.env.WORK_MS || '20');
const PERMITS = 4;
let free = PERMITS;
const queue = [];
let arrived = 0;
let done = 0;
let inflight = 0;
let conns = 0;

function acquire() {
  if (free > 0) {
    free--;
    return Promise.resolve();
  }
  return new Promise((resolve) => queue.push(resolve));
}

function release() {
  const next = queue.shift();
  if (next) next();
  else free++;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = http.createServer(async (req, res) => {
  if (req.url === '/work') {
    arrived++;
    inflight++;
    await acquire();
    try {
      await sleep(WORK_MS);
    } finally {
      release();
    }
    done++;
    inflight--;
  }
  res.writeHead(200, { 'Content-Type': 'text/plain', 'Content-Length': '3' });
  res.end('ok\n');
});

server.on('connection', (s) => {
  conns++;
  s.on('close', () => conns--);
});

const h = monitorEventLoopDelay({ resolution: 10 });
h.enable();
let prev = Date.now() / 1000;
function tick() {
  const now = Date.now() / 1000;
  process.stdout.write(`stat ${prev.toFixed(3)} ${now.toFixed(3)} ${process.pid} ${arrived} ${done} ${queue.length} ${inflight}` +
    ` lag_p99_ms=${(h.percentile(99) / 1e6).toFixed(2)} lag_max_ms=${(h.max / 1e6).toFixed(2)} lag_min_ms=${(h.min / 1e6).toFixed(2)} lag_mean_ms=${(h.mean / 1e6).toFixed(2)} conns=${conns}\n`);
  arrived = 0;
  done = 0;
  h.reset();
  prev = now;
  setTimeout(tick, 1000 - (Date.now() % 1000));
}
setTimeout(tick, 1000 - (Date.now() % 1000));

process.on('SIGTERM', () => process.exit(143));
server.listen(8080, '0.0.0.0');
