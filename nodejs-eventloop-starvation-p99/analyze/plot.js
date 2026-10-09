const fs = require('fs')

const csvFile = process.argv[2] ?? 'results.csv'
const lines = fs.readFileSync(csvFile, 'utf8').trim().split('\n')
const [headerLine, ...dataLines] = lines
const keys = headerLine.split(',')

const rows = dataLines.map(l => {
  const vals = l.split(',')
  return Object.fromEntries(keys.map((k, i) => [k, vals[i]]))
})

const knobs = [...new Set(rows.map(r => r.knob))].sort((a, b) => +a - +b)

const lightRows = rows.filter(r => r.condition === 'light')
const syncRows = rows.filter(r => r.condition === 'sync-cpu')

function p99ByKnob(data) {
  return knobs.map(k => data.find(r => r.knob === k)?.p99 ?? 0)
}

const lightP99 = p99ByKnob(lightRows)
const syncP99 = p99ByKnob(syncRows)

function valueByKnob(data, key) {
  return knobs.map(k => data.find(r => r.knob === k)?.[key] ?? 'n/a')
}

const lightLag = valueByKnob(lightRows, 'lag_p99')
const syncCpu = valueByKnob(syncRows, 'cpu_pct')

function rate503(data) {
  return knobs.map(k => {
    const r = data.find(row => row.knob === k)
    if (!r) return 'n/a'
    const total = parseFloat(r.total)
    if (!total) return 'n/a'
    return (parseFloat(r.non2xx) / total * 100).toFixed(1) + '%'
  })
}

const hasShedding = rows.some(r => parseFloat(r.non2xx) > 0)
const light503 = rate503(lightRows)
const sync503 = rate503(syncRows)

const tableHeader = hasShedding
  ? '| sync weight | /sync-cpu p99 (ms) | /sync-cpu 503 rate (%) | /light p99 (ms) | /light 503 rate (%) | eventloop lag p99 (ms) |'
  : '| sync weight | system cpu (%) | eventloop lag p99 (ms) | /light p99 (ms) | /sync-cpu p99 (ms) |'
const tableSep = hasShedding
  ? '|---|---|---|---|---|---|'
  : '|---|---|---|---|---|'

const tableRows = knobs.map((k, i) => {
  if (hasShedding) {
    return `| ${k}ms | ${syncP99[i]} | ${sync503[i]} | ${lightP99[i]} | ${light503[i]} | ${lightLag[i]} |`
  }
  return `| ${k}ms | ${syncCpu[i]} | ${lightLag[i]} | ${lightP99[i]} | ${syncP99[i]} |`
})

const table = [tableHeader, tableSep, ...tableRows].join('\n')

const summary = `## Experiment 01 — Event Loop Starvation

Claim: p99 spikes even when CPU has headroom. The unrelated lightweight endpoint (/light) degrades too.

### Knob: sync blocking weight (${knobs.join(' / ')} ms)

${table}

> /light has no sync work. Measured concurrently with load on /sync-cpu.
`

process.stdout.write(summary)
