const http = require('http')

const BASE = process.env.APP_URL ?? 'http://localhost:3000'
const RATE = parseInt(process.env.RATE ?? '50', 10)
const DURATION = parseInt(process.env.DURATION ?? '30', 10)
const SYNC_MS = parseInt(process.env.SYNC_MS ?? '10000', 10)

const intervalMs = 1000 / RATE
let sent = 0
const start = Date.now()

setTimeout(() => process.exit(0), (DURATION + 2) * 1000)

const statusTimer = setInterval(() => {
  const elapsed = ((Date.now() - start) / 1000).toFixed(0)
  console.error(`t=${elapsed}s sent=${sent}`)
}, 5000)

const timer = setInterval(() => {
  if ((Date.now() - start) / 1000 >= DURATION) {
    clearInterval(timer)
    clearInterval(statusTimer)
    console.error(`sent=${sent} done`)
    return
  }
  sent++
  http.get(`${BASE}/sync-io?ms=${SYNC_MS}`, (res) => {
    res.resume()
  }).on('error', () => {})
}, intervalMs)
