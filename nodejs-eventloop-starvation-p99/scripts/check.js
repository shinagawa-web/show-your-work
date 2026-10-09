const fs = require('fs')

const [csvFile, mode] = process.argv.slice(2)
const reasons = []

const lines = fs.existsSync(csvFile) ? fs.readFileSync(csvFile, 'utf8').trim().split('\n').filter(Boolean) : []
const [headerLine, ...dataLines] = lines
const keys = headerLine ? headerLine.split(',') : []
const rows = dataLines.map(l => {
  const vals = l.split(',')
  return Object.fromEntries(keys.map((k, i) => [k, vals[i]]))
})

if (!lines.length) reasons.push(`${csvFile} is missing or empty`)
else if (!rows.length) reasons.push(`${csvFile} has no rows`)

for (const r of rows) {
  const at = `${r.condition} at sync_ms=${r.knob}`
  if (!(parseFloat(r.total) > 0)) reasons.push(`no request completed for ${at}`)
  if (r.cpu_pct === 'n/a' || r.lag_p99 === 'n/a') reasons.push(`/metrics could not be read for ${at}`)
  if (mode === 'no-shed' && parseFloat(r.non2xx) > 0) reasons.push(`${r.non2xx} non-2xx responses without shedding for ${at}`)
}

for (const reason of reasons) process.stdout.write(`\nRUN INVALID: ${reason}\n`)
process.exit(reasons.length ? 1 : 0)
