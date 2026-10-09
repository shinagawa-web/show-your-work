const fs = require('fs')
const path = require('path')

const scenarioName = process.argv[2] ?? 'exp01'
const scenario = require('./scenarios/' + scenarioName)
const outFile = process.env.CSV_OUT ?? 'results.csv'

const header = 'stage,condition,concurrency,knob,rps,p50,p99,errors,timeouts,non2xx,total,cpu_pct,lag_p99'
const rows = [header]

function rowFrom(s) {
  return [s.label, s.condition, s.concurrency, s.knob, s.rps, s.p50, s.p99, s.errors, s.timeouts, s.non2xx ?? 0, s.total ?? 0, s.cpu_pct ?? 'n/a', s.lag_p99 ?? 'n/a'].join(',')
}

;(async () => {
  for (const ms of scenario.syncMs) {
    process.stderr.write(`=== sync ${ms}ms ===\n`)
    const pair = await scenario.runPair(ms)
    for (const s of pair) {
      const row = rowFrom(s)
      rows.push(row)
      process.stderr.write(row + '\n')
    }
  }

  const out = rows.join('\n') + '\n'
  fs.writeFileSync(path.resolve(outFile), out)
  process.stdout.write(out)
})()
