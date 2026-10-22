const http = require('http');
const fs = require('fs');

const port = Number(process.env.PORT || 8081);
const backlog = Number(process.env.BACKLOG || 100);
const idleMs = Number(process.env.IDLE_MS || 75000);
const log = fs.createWriteStream(process.env.LOG || '/results/app-access.log', { flags: 'a' });

const server = http.createServer((req, res) => {
  const t0 = process.hrtime.bigint();
  const start = Date.now();
  res.on('finish', () => {
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    log.write(`${(start / 1000).toFixed(3)} ${req.headers['x-request-id'] || '-'} ${ms.toFixed(3)}\n`);
  });
  res.writeHead(200, { 'Content-Type': 'text/plain', 'Content-Length': 3 });
  res.end('ok\n');
});

server.keepAliveTimeout = idleMs;
server.headersTimeout = idleMs + 1000;

server.listen({ port, host: '0.0.0.0', backlog }, () => {
  process.stderr.write(`listening on ${port} backlog=${backlog} keepAliveTimeout=${idleMs}ms\n`);
});
