const { shedTotal } = require('../metrics')

const CHECK_INTERVAL = 100

let lagMs = 0

;(function measure() {
  const start = Date.now()
  setTimeout(() => {
    lagMs = Math.max(0, Date.now() - start - CHECK_INTERVAL)
    measure()
  }, CHECK_INTERVAL).unref()
})()

function shedEventloop(thresholdMs) {
  return (req, res, next) => {
    if (lagMs > thresholdMs) {
      shedTotal.inc({ reason: 'eventloop_lag' })
      res.setHeader('Retry-After', '1')
      return res.status(503).json({ error: 'overloaded', lag_ms: lagMs.toFixed(1) })
    }
    next()
  }
}

module.exports = shedEventloop
module.exports.getLagMs = () => lagMs
