import http from 'k6/http';
import exec from 'k6/execution';

const BASE = __ENV.BASE;
const KEYS = Number(__ENV.KEYS);
const S = Number(__ENV.ZIPF_S);

const cdf = [];
let sum = 0;
for (let k = 1; k <= KEYS; k++) {
  sum += Math.pow(k, -S);
  cdf.push(sum);
}
for (let i = 0; i < KEYS; i++) cdf[i] /= sum;

function key() {
  const u = Math.random();
  let lo = 0;
  let hi = KEYS - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cdf[mid] < u) lo = mid + 1;
    else hi = mid;
  }
  return lo + 1;
}

export const options = {
  noConnectionReuse: true,
  discardResponseBodies: true,
  scenarios: {
    load: {
      executor: 'ramping-arrival-rate',
      startRate: Number(__ENV.START_RATE),
      timeUnit: '1s',
      stages: [
        { target: Number(__ENV.RATE), duration: `${__ENV.RAMP}s` },
        { target: Number(__ENV.RATE), duration: `${Number(__ENV.SECONDS) - Number(__ENV.RAMP)}s` },
      ],
      preAllocatedVUs: 6000,
      maxVUs: 12000,
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(99)', 'max'],
};

export default function () {
  if (exec.scenario.iterationInTest === 0) console.log(`T0 ${exec.scenario.startTime}`);
  const t = Date.now();
  const res = http.get(`${BASE}/item?id=${key()}`, { timeout: '30s' });
  console.log(`r ${t} ${Date.now() - t} ${res.timings.duration.toFixed(3)} ${res.status}`);
}

export function handleSummary(data) {
  return { '/out/summary.json': JSON.stringify(data) };
}
