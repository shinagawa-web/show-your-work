import http from 'k6/http';
import { Gauge } from 'k6/metrics';

const RATE = Number(__ENV.RATE);
const WINDOW = `${__ENV.WINDOW}s`;
const K6_TIMEOUT = __ENV.K6_TIMEOUT || '60s';
const STATS = 'http://127.0.0.1:15000/stats?filter=app%5C.demo.*outlier_detection%5C.ejections_active';

const active = new Gauge('od_ejections_active');

export const options = {
  scenarios: {
    load: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: WINDOW,
            preAllocatedVUs: 50, maxVUs: 200, exec: 'load' },
    poll: { executor: 'constant-arrival-rate', rate: 5, timeUnit: '1s', duration: WINDOW,
            preAllocatedVUs: 2, maxVUs: 5, exec: 'poll' },
  },
};

export function load() {
  http.get('http://app.demo.svc.cluster.local:8080/', { responseType: 'none', timeout: K6_TIMEOUT, tags: { ep: 'app' } });
}

export function poll() {
  const res = http.get(STATS, { responseType: 'text', tags: { ep: 'admin' } });
  const m = String(res.body).match(/ejections_active: (\d+)/);
  if (m) active.add(Number(m[1]));
}
