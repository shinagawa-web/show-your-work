const express = require('express')
const { register } = require('./metrics')

const app = express()

if (process.env.SHED_THRESHOLD_MS) {
  const shedEventloop = require('./middleware/shed-eventloop')
  app.use((req, res, next) => {
    if (req.path === '/metrics') return next()
    shedEventloop(parseInt(process.env.SHED_THRESHOLD_MS, 10))(req, res, next)
  })
}

app.use(require('./routes/light'))
app.use(require('./routes/sync-cpu'))

app.get('/metrics', async (req, res) => {
  res.set('Content-Type', register.contentType)
  res.end(await register.metrics())
})

app.get('/lag', (req, res) => {
  const shed = process.env.SHED_THRESHOLD_MS
    ? require('./middleware/shed-eventloop').getLagMs()
    : null
  res.json({ lag_ms: shed })
})

const port = process.env.PORT ?? 3000
app.listen(port, () => {
  process.stderr.write(`listening on ${port}\n`)
})
