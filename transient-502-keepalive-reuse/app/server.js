const http = require('http');
const fs = require('fs');

const port = Number(process.env.PORT || 8081);
const slow = Number(process.env.SLOW_MS || 0);
const noKeepalive = process.env.NO_KEEPALIVE === '1';
const logPath = process.env.LOG;
const stream = logPath ? fs.createWriteStream(logPath, { flags: 'a' }) : null;

const server = http.createServer((req, res) => {
  if (req.url === '/__close-idle') {
    res.writeHead(200).end('closing\n');
    server.closeIdleConnections();
    return;
  }
  let body = 0;
  req.on('data', (c) => { body += c.length; });
  req.on('end', () => {
    if (stream) stream.write(`${Date.now()} ${req.method} ${req.url} ${body} ${req.socket.remotePort}\n`);
    const done = () => {
      const payload = 'ok\n';
      const headers = { 'Content-Type': 'text/plain', 'Content-Length': Buffer.byteLength(payload) };
      if (noKeepalive) headers['Connection'] = 'close';
      res.writeHead(200, headers);
      res.end(payload);
    };
    if (slow > 0) setTimeout(done, slow); else done();
  });
});

if (process.env.KEEPALIVE_MS) {
  server.keepAliveTimeout = Number(process.env.KEEPALIVE_MS);
  server.headersTimeout = server.keepAliveTimeout + 1000;
}

server.listen(port, '127.0.0.1', () => {
  process.stderr.write(`listening on ${port} keepAliveTimeout=${server.keepAliveTimeout}ms slow=${slow}ms noKeepalive=${noKeepalive}\n`);
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    server.closeIdleConnections();
    server.close(() => process.exit(0));
    const killMs = Number(process.env.SHUTDOWN_KILL_MS || 0);
    if (killMs > 0) setTimeout(() => process.exit(0), killMs).unref();
  });
}
