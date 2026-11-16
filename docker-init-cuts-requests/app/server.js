const express = require('express');

const app = express();

app.get('/slow', (req, res) => {
  const seconds = Number(req.query.seconds || 2);
  console.log(`request accepted: GET /slow seconds=${seconds}`);
  setTimeout(() => {
    res.send(`done after ${seconds}s\n`);
    console.log(`response sent: GET /slow seconds=${seconds}`);
  }, seconds * 1000);
});

const server = app.listen(3000, () => {
  console.log(`listening on 3000 pid=${process.pid}`);
});

if (process.env.SIGTERM_HANDLER === '1') {
  process.on('SIGTERM', () => {
    console.log('SIGTERM received: closing the server');
    server.close(() => {
      console.log('server closed: exiting');
      process.exit(0);
    });
  });
}
