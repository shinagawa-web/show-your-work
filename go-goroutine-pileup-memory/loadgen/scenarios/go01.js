const autocannon = require('autocannon')
const http = require('http')

const BASE = process.env.APP_URL ?? 'http://localhost:3000'
const CONCURRENCY = parseInt(process.env.CONCURRENCY ?? '50', 10)
const DURATION = parseInt(process.env.DURATION ?? '30', 10)
const WARMUP = parseInt(process.env.WARMUP ?? '10', 10)
const TIMEOUT = parseInt(process.env.REQ_TIMEOUT ?? '120', 10)
const ARM = process.env.ARM ?? 'a'

const syncMs = [0, 50, 100, 150, 200]

function fetchMetrics() {
  return new Promise((resolve) => {
    http.get(`${BASE}/metrics`, (res) => {
      let body = ''
      res.on('data', d => { body += d })
      res.on('end', () => {
        const goroutines = body.match(/^go_goroutines_peak\s+([\d.]+)/m)
        const mem = body.match(/^go_mem_mb\s+([\d.]+)/m)
        resolve({
          goroutines_peak: goroutines ? goroutines[1] : 'n/a',
          mem_mb: mem ? parseFloat(mem[1]).toFixed(1) : 'n/a',
        })
      })
    }).on('error', () => resolve({ goroutines_peak: 'n/a', mem_mb: 'n/a' }))
  })
}

function pct(sorted, p) {
  if (!sorted.length) return 0
  return sorted[Math.max(0, Math.ceil(sorted.length * p / 100) - 1)]
}

function makeRequests(ms, tracking) {
  return [{
    setupRequest(req, context) {
      context.route = Math.random() < 1 / 3 ? 'sync-cpu' : 'light'
      context.start = Date.now()
      return Object.assign({}, req, {
        path: context.route === 'sync-cpu' ? `/sync-cpu?ms=${ms}` : '/light',
      })
    },
    onResponse(status, body, context) {
      tracking[context.route].latencies.push(Date.now() - context.start)
      if (status >= 300) tracking[context.route].non2xx++
    },
  }]
}

function emptyTracking() {
  return {
    'sync-cpu': { latencies: [], non2xx: 0 },
    light: { latencies: [], non2xx: 0 },
  }
}

async function runPair(ms) {
  if (WARMUP > 0) {
    await autocannon({
      url: BASE,
      connections: CONCURRENCY,
      duration: WARMUP,
      timeout: TIMEOUT,
      silent: true,
      requests: makeRequests(ms, emptyTracking()),
    })
  }

  const perRoute = emptyTracking()

  await autocannon({
    url: BASE,
    connections: CONCURRENCY,
    duration: DURATION,
    timeout: TIMEOUT,
    requests: makeRequests(ms, perRoute),
  })

  const { goroutines_peak, mem_mb } = await fetchMetrics()

  return ['sync-cpu', 'light'].map(name => {
    const { latencies, non2xx } = perRoute[name]
    latencies.sort((a, b) => a - b)
    const total = latencies.length
    return {
      label: `sync${ms}ms-${name === 'sync-cpu' ? 'synccpu' : 'light'}`,
      condition: name,
      concurrency: CONCURRENCY,
      knob: ms,
      arm: ARM,
      rps: (total / DURATION).toFixed(1),
      p50: pct(latencies, 50),
      p99: pct(latencies, 99),
      non2xx,
      total,
      goroutines_peak,
      mem_mb,
    }
  })
}

module.exports = { syncMs, runPair }
