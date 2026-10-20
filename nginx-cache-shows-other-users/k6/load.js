import http from 'k6/http';
import { Counter } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://nginx';
function required(name) {
  if (!__ENV[name]) throw new Error(`${name} is not set`);
  return __ENV[name];
}
const RATE = Number(required('RATE'));
const DURATION = required('DURATION');
const LOGIN_SHARE = Number(required('LOGIN_SHARE'));
const USERS = Number(required('USERS'));
const PAGES = Number(required('PAGES'));
const OUT = __ENV.OUT || '/results/k6-summary.json';

export const options = {
  scenarios: {
    load: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION,
            preAllocatedVUs: 100, maxVUs: 1000 },
  },
  setupTimeout: '60s',
};

const total = new Counter('req_total');
const loggedIn = new Counter('req_logged_in');
const otherScreen = new Counter('other_user_screen');
const loggedOutScreen = new Counter('logged_in_got_logged_out_screen');
const invalid = new Counter('invalid_response');

const userName = (i) => `u${String(i).padStart(3, '0')}`;

export function setup() {
  const sessions = {};
  for (let i = 1; i <= USERS; i++) {
    const u = userName(i);
    const res = http.get(`${BASE}/login?user=${u}`, { tags: { name: 'login' } });
    const m = /session=([0-9a-f]+)/.exec(res.headers['Set-Cookie'] || '');
    if (res.status !== 200 || !m) throw new Error(`login ${u} failed: ${res.status}`);
    sessions[u] = m[1];
  }
  return sessions;
}

export default function (sessions) {
  const page = 1 + Math.floor(Math.random() * PAGES);
  const params = { tags: { name: 'items' }, headers: {} };
  let sender = 'anon';
  if (Math.random() < LOGIN_SHARE) {
    sender = userName(1 + Math.floor(Math.random() * USERS));
    params.headers.Cookie = `session=${sessions[sender]}`;
  }
  const t0 = Date.now();
  const res = http.get(`${BASE}/items/${page}`, params);
  const t1 = Date.now();
  const m = /<!-- for:(\S+) page:(\d+) -->/.exec(res.body || '');
  const got = m ? m[1] : 'none';
  const ok = res.status === 200 && m && Number(m[2]) === page;
  total.add(1);
  if (sender !== 'anon') loggedIn.add(1);
  if (!ok) invalid.add(1);
  else if (got !== 'anon' && got !== sender) otherScreen.add(1);
  else if (sender !== 'anon' && got === 'anon') loggedOutScreen.add(1);
  console.log(`R ${t0} ${t1} ${page} ${sender} ${got} ${res.status}`);
}

export function handleSummary(data) {
  const c = (name) => (data.metrics[name] ? data.metrics[name].values.count : 0);
  const out = {
    req_total: c('req_total'),
    req_logged_in: c('req_logged_in'),
    other_user_screen: c('other_user_screen'),
    logged_in_got_logged_out_screen: c('logged_in_got_logged_out_screen'),
    invalid_response: c('invalid_response'),
    dropped_iterations: c('dropped_iterations'),
    vus_max: data.metrics.vus_max ? data.metrics.vus_max.values.max : null,
    test_run_duration_ms: data.state.testRunDurationMs,
    config: { DURATION, LOGIN_SHARE: __ENV.LOGIN_SHARE, PAGES: __ENV.PAGES, RATE: __ENV.RATE, USERS: __ENV.USERS },
  };
  return { [OUT]: JSON.stringify(out, null, 2) + '\n', stdout: JSON.stringify(out) + '\n' };
}
