const { Router } = require('express')
const { httpLatency } = require('../metrics')

const router = Router()

const PAYLOAD = JSON.stringify(
  Array.from({ length: 1000 }, (_, i) => ({ id: i, name: `item-${i}`, value: 'x'.repeat(200) }))
)

function blockEventLoop(ms) {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    JSON.parse(PAYLOAD)
  }
}

router.get('/sync-cpu', (req, res) => {
  const start = Date.now()
  const ms = parseInt(req.query.ms ?? process.env.SYNC_MS ?? '0', 10)
  blockEventLoop(ms)
  res.json({ ok: true, blocked: ms })
  httpLatency.observe({ method: 'GET', route: '/sync-cpu', status: 200 }, Date.now() - start)
})

module.exports = router
