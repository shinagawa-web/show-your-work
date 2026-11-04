import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';

const BASE = __ENV.BASE;
const KEYS = Number(__ENV.KEYS);
const S = Number(__ENV.ZIPF_S);

const cdf = new SharedArray('cdf', () => {
  const out = [];
  let sum = 0;
  for (let k = 1; k <= KEYS; k++) {
    sum += Math.pow(k, -S);
    out.push(sum);
  }
  return out.map((v) => v / sum);
});

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
      preAllocatedVUs: Number(__ENV.PREALLOC_VUS),
      maxVUs: Number(__ENV.MAX_VUS),
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(99)', 'max'],
};

export function setup() {
  if (__ENV.PREFILL !== '1') return;
  for (let id = 1; id <= KEYS; id++) http.get(`${BASE}/item?id=${id}`, { timeout: '30s' });
}

export default function () {
  if (exec.scenario.iterationInTest === 0) console.log(`T0 ${exec.scenario.startTime}`);
  const t = Date.now();
  const res = http.get(`${BASE}/item?id=${key()}`, { timeout: '30s' });
  console.log(`r ${t} ${Date.now() - t} ${res.timings.duration.toFixed(3)} ${res.status}`);
}

export function handleSummary(data) {
  return { '/out/summary.json': JSON.stringify(data) };
}
