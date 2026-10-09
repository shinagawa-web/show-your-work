const fs = require('fs')
const path = require('path')

const scenarioName = process.argv[2] ?? 'go01'
const scenario = require('./scenarios/' + scenarioName)
const outFile = process.env.CSV_OUT ?? 'results-go.csv'

const header = 'stage,condition,concurrency,knob,arm,rps,p50,p99,non2xx,total,goroutines_peak,mem_mb'
const rows = [header]

function rowFrom(s) {
  return [s.label, s.condition, s.concurrency, s.knob, s.arm, s.rps, s.p50, s.p99, s.non2xx ?? 0, s.total ?? 0, s.goroutines_peak ?? 'n/a', s.mem_mb ?? 'n/a'].join(',')
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
