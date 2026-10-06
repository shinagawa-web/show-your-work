// Open-model load. Scenario shape comes from env vars:
//   RATE, DURATION           constant-arrival-rate for the main scenario
//   HEAVY_BEFORE, HEAVY_AFTER fraction of requests to /api/heavy before/after SWITCH_AT
//   SWITCH_AT                seconds; also when ADMIN_QUERY is sent to downstream /admin
//   ADMIN_QUERY              e.g. "delay_light=120" (empty: no admin call)
//   TENANT_MODE=1            per-tenant scenarios (see tenant* below) instead of the main one
//   TENANT_PICK              tenant of each request: "random" (uniform, independent per request)
//                            or "rr" (iterationInTest % N: each tenant gets a fixed arrival phase)
//   ARRIVAL                  tenant scenarios only. "fixed": one request per iteration, evenly
//                            spaced. "bernoulli": the executor runs THIN times the rate and each
//                            iteration sends with probability 1/THIN, which gives random
//                            (approximately Poisson) arrivals at the same mean rate.
//   THIN                     grid factor for ARRIVAL=bernoulli (default 10)
import http from 'k6/http';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://nginx';
const RATE = Number(__ENV.RATE || 200);
const DURATION = __ENV.DURATION || '25s';
const SWITCH_AT = Number(__ENV.SWITCH_AT || 8);
const HEAVY_BEFORE = Number(__ENV.HEAVY_BEFORE || 0);
const HEAVY_AFTER = Number(__ENV.HEAVY_AFTER || 0);
const ADMIN_QUERY = __ENV.ADMIN_QUERY || '';
const TENANT_MODE = __ENV.TENANT_MODE === '1';
const TENANT_RATE = Number(__ENV.TENANT_RATE || 40);
const EARLY_TENANTS = Number(__ENV.EARLY_TENANTS || 3);
const TOTAL_TENANTS = Number(__ENV.TOTAL_TENANTS || 10);
const TENANT_PICK = __ENV.TENANT_PICK || 'random';
const ARRIVAL = __ENV.ARRIVAL || 'fixed';
const THIN = ARRIVAL === 'bernoulli' ? Number(__ENV.THIN || 10) : 1;

const vus = { preAllocatedVUs: 600, maxVUs: 3000 };
const scenarios = {};
if (TENANT_MODE) {
  scenarios.tenant_early = { executor: 'constant-arrival-rate', rate: TENANT_RATE * EARLY_TENANTS * THIN, timeUnit: '1s', duration: DURATION, exec: 'tenantEarly', ...vus };
  scenarios.tenant_late = { executor: 'constant-arrival-rate', rate: TENANT_RATE * (TOTAL_TENANTS - EARLY_TENANTS) * THIN, timeUnit: '1s', startTime: `${SWITCH_AT}s`, duration: `${parseInt(DURATION) - SWITCH_AT}s`, exec: 'tenantLate', ...vus };
} else {
  scenarios.load = { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION, exec: 'load', ...vus };
}
if (ADMIN_QUERY) {
  scenarios.switch = { executor: 'per-vu-iterations', vus: 1, iterations: 1, startTime: `${SWITCH_AT}s`, exec: 'switchFn' };
}

export const options = {
  scenarios,
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'med', 'p(99)', 'max'],
};

function hit(ep, tenant) {
  http.get(`${BASE}/api/${ep}`, { headers: { 'X-Tenant': tenant }, tags: { ep, tenant }, timeout: '60s' });
}

export function load() {
  const t = exec.instance.currentTestRunDuration / 1000;
  const frac = t < SWITCH_AT ? HEAVY_BEFORE : HEAVY_AFTER;
  hit(Math.random() < frac ? 'heavy' : 'light', 'none');
}

// first + (one of n tenants)
function tenant(first, n) {
  const i = TENANT_PICK === 'rr' ? exec.scenario.iterationInTest % n : Math.floor(Math.random() * n);
  return `t${first + i}`;
}

function tenantHit(first, n) {
  if (THIN > 1 && Math.random() >= 1 / THIN) return;
  hit('light', tenant(first, n));
}

export function tenantEarly() {
  tenantHit(0, EARLY_TENANTS);
}

export function tenantLate() {
  tenantHit(EARLY_TENANTS, TOTAL_TENANTS - EARLY_TENANTS);
}

export function switchFn() {
  http.get(`http://downstream:9001/admin?${ADMIN_QUERY}`, { tags: { ep: 'admin', tenant: 'admin' } });
}
