import http from 'k6/http';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://nginx';
const ADMIN = __ENV.ADMIN || 'http://app:9001';
const ITER_RATE = Number(__ENV.ITER_RATE || 600);
const DURATION = __ENV.DURATION || '25s';
const SWITCH_AT = Number(__ENV.SWITCH_AT || 8);
const TENANT_RATE = Number(__ENV.TENANT_RATE || 20);
const TENANTS_BEFORE = Number(__ENV.TENANTS_BEFORE || 10);
const TENANTS_AFTER = Number(__ENV.TENANTS_AFTER || 10);
const HEAVY_AFTER = Number(__ENV.HEAVY_AFTER || 0);
const ADMIN_QUERY = __ENV.ADMIN_QUERY || '';
const K6_TIMEOUT = __ENV.K6_TIMEOUT || '60s';

function step(n) {
  const s = ITER_RATE / (n * TENANT_RATE);
  if (!Number.isInteger(s) || s < 1) throw new Error(`ITER_RATE / (tenants * TENANT_RATE) must be a positive integer, got ${s}`);
  return s;
}
const STEP_BEFORE = step(TENANTS_BEFORE);
const STEP_AFTER = step(TENANTS_AFTER);

export const options = {
  scenarios: {
    load: { executor: 'constant-arrival-rate', rate: ITER_RATE, timeUnit: '1s', duration: DURATION,
            preAllocatedVUs: 1500, maxVUs: 4000, exec: 'load' },
    switch: { executor: 'per-vu-iterations', vus: 1, iterations: 1, startTime: `${SWITCH_AT}s`, exec: 'switchFn' },
  },
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'med', 'p(99)', 'max'],
};

export function load() {
  const after = exec.instance.currentTestRunDuration >= SWITCH_AT * 1000;
  const s = after ? STEP_AFTER : STEP_BEFORE;
  if (exec.scenario.iterationInTest % s !== 0) return;
  const n = after ? TENANTS_AFTER : TENANTS_BEFORE;
  const tenant = `t${Math.floor(Math.random() * n)}`;
  const ep = after && Math.random() < HEAVY_AFTER ? 'heavy' : 'light';
  http.get(`${BASE}/api/${ep}`, { headers: { 'X-Tenant': tenant }, tags: { ep, tenant }, timeout: K6_TIMEOUT });
}

export function switchFn() {
  console.log(`T0_MS ${Math.round(Date.now() - exec.instance.currentTestRunDuration)}`);
  const get = (q) => http.get(`${ADMIN}/admin${q}`, { responseType: 'text', tags: { ep: 'admin', tenant: 'admin' } }).body;
  console.log(`ADMIN_BEFORE ${get('')}`);
  if (ADMIN_QUERY) console.log(`ADMIN_SET ${get(`?${ADMIN_QUERY}`)}`);
  console.log(`ADMIN_AFTER ${get('')}`);
}
