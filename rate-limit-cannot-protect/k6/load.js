// Open-model load. One constant-arrival-rate schedule for every scenario:
//   ITER_RATE iterations/s (default 600) for DURATION (default 25s), evenly spaced.
//   Each tenant sends TENANT_RATE r/s (default 20). With N tenants an iteration sends a
//   request when iterationInTest % (ITER_RATE / (N * TENANT_RATE)) == 0, so 10 tenants
//   send every 3rd iteration (200 r/s, one request every 5 ms) and 30 tenants send every
//   iteration (600 r/s). The tenant of each request is picked at random among the N.
// Scenario shape (env vars):
//   SWITCH_AT       seconds since the test start; the "after" values apply from here
//   TENANTS_BEFORE  tenants t0..t(N-1) before SWITCH_AT (default 10)
//   TENANTS_AFTER   tenants from SWITCH_AT (default 10)
//   HEAVY_AFTER     share of requests sent to /api/heavy from SWITCH_AT (default 0; before: 0)
//   ADMIN_QUERY     sent to the downstream /admin at SWITCH_AT (empty: no change)
//   K6_TIMEOUT      client timeout of each request (default 60s)
// At SWITCH_AT the "switch" scenario logs (raw console lines):
//   T0_MS <unix ms of the test start>
//   ADMIN_BEFORE <GET /admin>
//   ADMIN_SET <GET /admin?ADMIN_QUERY>     (only when ADMIN_QUERY is set)
//   ADMIN_AFTER <GET /admin>
import http from 'k6/http';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://nginx';
const ADMIN = __ENV.ADMIN || 'http://downstream:9001';
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
