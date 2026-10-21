const http = require('node:http');
const fs = require('node:fs');

const port = Number(process.env.PORT);
const requests = fs.openSync(process.env.REQUEST_LOG, 'a');
const now = () => (performance.timeOrigin + performance.now()) / 1000;

const server = http.createServer((req, res) => {
  fs.writeSync(requests, `${now().toFixed(6)} ${port} ${req.method} ${req.url} ${req.headers['x-request-id']} ${req.socket.remotePort}\n`);
  req.resume();
  req.on('end', () => res.end('ok\n'));
});

server.listen(port, '127.0.0.1');
