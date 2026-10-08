
export const AGES = { '10m': 600, '100m': 6000, '1000m': 60000, '6d': 518400 };
export const FRACTIONS = [0.05, 0.09, 0.11, 0.2];
export const REPEATS = 3;
export const MAX_AGE_SEC = 900;
export const MAX_AGE_POINTS = [300, 540, 660, 840, 960, 1200];
export const MAX_AGE_OTHER_AGES = ['10m', '1000m'];
export const MAX_AGE_OTHER_POINTS = [840, 960];
export const MAX_AGE_300_SEC = 300;
export const MAX_AGE_300_POINTS = { '100m': [240, 360, 540, 660], '1000m': [240, 360] };
export const DEPLOY_TIMINGS = ['early', 'mid', 'late'];
export const LATE_DEPLOY_LEAD_SEC = 3;

const pct = (f) => `p${String(Math.round(f * 100)).padStart(2, '0')}`;

function users(prefix, n, extra) {
  return Array.from({ length: n }, (_, i) => ({ id: `${prefix}-${i + 1}`, repeat: i + 1, ...extra }));
}

export function buildPlan() {
  const groups = [];

  groups.push({
    id: 'cal-wait-10m',
    set: 'cal-wait',
    ageSec: AGES['10m'],
    v1: { image: 'v1' },
    v2: { image: 'v2' },
    users: [0.05, 0.09, 0.11].flatMap((f) =>
      users(`cal-wait-10m-${pct(f)}-newtab`, REPEATS, {
        method: 'newtab',
        point: pct(f),
        realWaitSec: Math.round(f * AGES['10m']),
      })),
  });
  for (const f of [0.05, 0.09, 0.11]) {
    const age = Math.round(f * AGES['10m']);
    groups.push({
      id: `cal-age-10m-${pct(f)}`,
      set: 'cal-age',
      ageSec: AGES['10m'],
      ageHeader: age,
      v1: { image: 'v1' },
      v2: { image: 'v2' },
      users: users(`cal-age-10m-${pct(f)}-newtab`, REPEATS, { method: 'newtab', point: pct(f) }),
    });
  }

  for (const [ageName, ageSec] of Object.entries(AGES)) {
    for (const f of FRACTIONS) {
      const g = {
        id: `grid-${ageName}-${pct(f)}`,
        set: 'grid',
        ageName,
        ageSec,
        ageHeader: Math.round(f * ageSec),
        v1: { image: 'v1' },
        v2: { image: 'v2' },
        users: users(`grid-${ageName}-${pct(f)}-newtab`, REPEATS, { method: 'newtab', point: pct(f) }),
      };
      if (ageName === '100m') {
        g.users.push(...users(`grid-${ageName}-${pct(f)}-reload`, REPEATS, { method: 'reload', point: pct(f) }));
      }
      groups.push(g);
    }
  }

  const a = AGES['100m'];
  const factor = (set, v1, v2, points = FRACTIONS.map((f) => [pct(f), Math.round(f * a)])) => {
    for (const [point, age] of points) {
      groups.push({
        id: `${set}-100m-${point}`,
        set,
        ageName: '100m',
        ageSec: a,
        ageHeader: age,
        v1,
        v2,
        users: users(`${set}-100m-${point}-newtab`, REPEATS, { method: 'newtab', point }),
      });
    }
  };
  factor('keep', { image: 'v1' }, { image: 'v2-keep' });
  factor('spa', { image: 'v1', env: { SPA_FALLBACK: '1' } }, { image: 'v2', env: { SPA_FALLBACK: '1' } });
  factor('nocache',
    { image: 'v1', env: { INDEX_CACHE_CONTROL: 'no-cache' } },
    { image: 'v2', env: { INDEX_CACHE_CONTROL: 'no-cache' } });
  factor('maxage',
    { image: 'v1', env: { INDEX_CACHE_CONTROL: `max-age=${MAX_AGE_SEC}` } },
    { image: 'v2', env: { INDEX_CACHE_CONTROL: `max-age=${MAX_AGE_SEC}` } },
    MAX_AGE_POINTS.map((s) => [`s${s}`, s]));
  factor('reloadfail', { image: 'v1-reload' }, { image: 'v2-reload' });

  for (const ageName of MAX_AGE_OTHER_AGES) {
    for (const s of MAX_AGE_OTHER_POINTS) {
      const cc = { INDEX_CACHE_CONTROL: `max-age=${MAX_AGE_SEC}` };
      groups.push({
        id: `maxage-${ageName}-s${s}`,
        set: 'maxage',
        ageName,
        ageSec: AGES[ageName],
        ageHeader: s,
        v1: { image: 'v1', env: cc },
        v2: { image: 'v2', env: cc },
        users: users(`maxage-${ageName}-s${s}-newtab`, REPEATS, { method: 'newtab', point: `s${s}` }),
      });
    }
  }

  for (const [ageName, points] of Object.entries(MAX_AGE_300_POINTS)) {
    for (const s of points) {
      const cc = { INDEX_CACHE_CONTROL: `max-age=${MAX_AGE_300_SEC}` };
      groups.push({
        id: `maxage300-${ageName}-s${s}`,
        set: 'maxage300',
        ageName,
        ageSec: AGES[ageName],
        ageHeader: s,
        v1: { image: 'v1', env: cc },
        v2: { image: 'v2', env: cc },
        users: users(`maxage300-${ageName}-s${s}-newtab`, REPEATS, { method: 'newtab', point: `s${s}` }),
      });
    }
  }

  for (const timing of DEPLOY_TIMINGS) {
    for (const f of FRACTIONS) {
      groups.push({
        id: `deploytime-10m-${timing}-${pct(f)}`,
        set: 'deploytime',
        ageName: '10m',
        ageSec: AGES['10m'],
        deployAt: timing,
        v1: { image: 'v1' },
        v2: { image: 'v2' },
        users: users(`deploytime-10m-${timing}-${pct(f)}-newtab`, REPEATS, {
          method: 'newtab',
          point: pct(f),
          realWaitSec: Math.round(f * AGES['10m']),
        }),
      });
    }
  }

  for (const deploy of [false, true]) {
    for (const f of FRACTIONS) {
      const env = { INDEX_CACHE_CONTROL: 'no-cache', LOG_IF_HEADERS: '1' };
      const name = deploy ? 'deploy' : 'nodeploy';
      groups.push({
        id: `nocache304-100m-${name}-${pct(f)}`,
        set: 'nocache304',
        ageName: '100m',
        ageSec: a,
        ageHeader: Math.round(f * a),
        deploy,
        v1: { image: 'v1', env },
        v2: { image: 'v2', env },
        users: users(`nocache304-100m-${name}-${pct(f)}-newtab`, REPEATS, { method: 'newtab', point: pct(f) }),
      });
    }
  }

  groups.push({
    id: 'pressfirst-wait-10m',
    set: 'pressfirst',
    ageName: '10m',
    ageSec: AGES['10m'],
    pressFirst: true,
    v1: { image: 'v1' },
    v2: { image: 'v2' },
    users: [0.05, 0.09, 0.11].flatMap((f) =>
      users(`pressfirst-wait-10m-${pct(f)}-newtab`, REPEATS, {
        method: 'newtab',
        point: pct(f),
        realWaitSec: Math.round(f * AGES['10m']),
      })),
  });
  for (const f of FRACTIONS) {
    groups.push({
      id: `pressfirst-100m-${pct(f)}`,
      set: 'pressfirst',
      ageName: '100m',
      ageSec: a,
      ageHeader: Math.round(f * a),
      pressFirst: true,
      v1: { image: 'v1' },
      v2: { image: 'v2' },
      users: users(`pressfirst-100m-${pct(f)}-newtab`, REPEATS, { method: 'newtab', point: pct(f) }),
    });
  }

  return groups;
}

export const IMAGES = {
  v1: { VERSION: 'v1' },
  v2: { VERSION: 'v2' },
  'v2-keep': { VERSION: 'v2', KEEP_OLD_ASSETS: '1' },
  'v1-reload': { VERSION: 'v1', RELOAD_ON_PRELOAD_ERROR: '1' },
  'v2-reload': { VERSION: 'v2', RELOAD_ON_PRELOAD_ERROR: '1' },
};
