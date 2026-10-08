const os = require('os')
const { monitorEventLoopDelay } = require('perf_hooks')
const client = require('prom-client')

const register = new client.Registry()
client.collectDefaultMetrics({ register })

const httpLatency = new client.Histogram({
  name: 'http_request_duration_ms',
  help: 'HTTP request latency in ms',
  labelNames: ['method', 'route', 'status'],
  buckets: [1, 5, 10, 25, 50, 100, 200, 500, 1000, 2000],
  registers: [register],
})

const loopLag = new client.Histogram({
  name: 'eventloop_lag_ms',
  help: 'Event loop delay in ms (monitorEventLoopDelay)',
  buckets: [1, 5, 10, 25, 50, 100, 200, 500],
  registers: [register],
})

const shedTotal = new client.Counter({
  name: 'shed_total',
  help: 'Total requests shed (503)',
  labelNames: ['reason'],
  registers: [register],
})

const loopLagP99 = new client.Gauge({
  name: 'eventloop_lag_p99_ms',
  help: 'Event loop delay p99 ms (last 1s window)',
  registers: [register],
})

const h = monitorEventLoopDelay({ resolution: 20 })
h.enable()

setInterval(() => {
  const lagMs = h.percentile(99) / 1e6
  loopLag.observe(lagMs)
  loopLagP99.set(lagMs)
  h.reset()
}, 1000).unref()

const cpuPercent = new client.Gauge({
  name: 'process_cpu_percent',
  help: 'CPU usage percent (1s window)',
  registers: [register],
})

let prevCpu = process.cpuUsage()
let prevTime = Date.now()

setInterval(() => {
  const now = Date.now()
  const cur = process.cpuUsage()
  const elapsedUs = (now - prevTime) * 1000
  const usedUs = (cur.user - prevCpu.user) + (cur.system - prevCpu.system)
  cpuPercent.set((usedUs / elapsedUs) * 100 / os.cpus().length)
  prevCpu = cur
  prevTime = now
}, 1000).unref()

module.exports = { register, httpLatency, shedTotal }
