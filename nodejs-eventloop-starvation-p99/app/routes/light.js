const { Router } = require('express')
const { httpLatency } = require('../metrics')

const router = Router()

router.get('/light', (req, res) => {
  const start = Date.now()
  res.json({ ok: true })
  httpLatency.observe({ method: 'GET', route: '/light', status: 200 }, Date.now() - start)
})

module.exports = router
