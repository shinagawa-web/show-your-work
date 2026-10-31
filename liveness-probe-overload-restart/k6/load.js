import http from 'k6/http';
import exec from 'k6/execution';

const BASE = __ENV.BASE;
const MODE = __ENV.MODE;

function scenario() {
  if (MODE === 'closed') {
    return { executor: 'constant-vus', vus: Number(__ENV.VUS), duration: __ENV.DURATION };
  }
  const rates = __ENV.RATES.split(',').map(Number);
  const durs = __ENV.DURS.split(',').map(Number);
  const stages = [];
  rates.forEach((r, i) => {
    if (i > 0) stages.push({ target: r, duration: '0s' });
    stages.push({ target: r, duration: `${durs[i]}s` });
  });
  return { executor: 'ramping-arrival-rate', startRate: rates[0], timeUnit: '1s',
           preAllocatedVUs: 1500, maxVUs: 1500, stages };
}

export const options = {
  scenarios: { load: scenario() },
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'med', 'p(99)', 'max'],
};

export default function () {
  if (exec.scenario.iterationInTest === 0) console.log(`T0 ${exec.scenario.startTime}`);
  const t = Date.now();
  const res = http.get(`${BASE}/work`, { timeout: '2s' });
  console.log(`r ${t} ${Date.now() - t} ${res.status} ${res.error_code} ${res.error}`);
}

export function handleSummary(data) {
  return { '/out/summary.json': JSON.stringify(data) };
}
