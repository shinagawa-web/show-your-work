import { MongoClient } from 'mongodb';

const mode = process.argv[2];
if (mode !== 'wait' && mode !== 'nowait') {
  console.error('usage: node scripts/test.mjs wait|nowait');
  process.exit(2);
}

const uri = process.env.MONGODB_URI || 'mongodb://localhost:27017/?directConnection=true';
const queryableDeadline = Number(process.env.QUERYABLE_DEADLINE_S || 180);
const now = () => Date.now() / 1000;
const emit = (o) => process.stdout.write(JSON.stringify(o) + '\n');

function describe(e) {
  return {
    class: e?.constructor?.name,
    name: e?.name,
    message: e?.message,
    code: e?.code,
    codeName: e?.codeName,
    errorLabels: Array.isArray(e?.errorLabels) ? e.errorLabels : undefined,
    cause: e?.cause ? { class: e.cause.constructor?.name, message: e.cause.message } : undefined,
  };
}

async function op(name, fn) {
  const t_start = now();
  try {
    const result = await fn();
    emit({ op: name, t_start, t_end: now(), ok: true, result });
    return result;
  } catch (e) {
    emit({ op: name, t_start, t_end: now(), ok: false, error: describe(e) });
    throw e;
  }
}

async function searchIndexState(coll) {
  const list = await coll.listSearchIndexes('default').toArray();
  return list.map((i) => ({ name: i.name, status: i.status, queryable: i.queryable }));
}

const client = new MongoClient(uri);
emit({ op: 'test', mode, driver: (await import('mongodb/package.json', { with: { type: 'json' } })).default.version, t_start: now() });
let rc = 0;
try {
  await op('connect', async () => {
    await client.connect();
    return null;
  });
  const coll = client.db('e2e').collection('movies');
  await op('insert', async () => {
    const docs = Array.from({ length: 100 }, (_, i) => ({ title: `movie ${i}`, n: i }));
    const r = await coll.insertMany(docs);
    return { insertedCount: r.insertedCount };
  });
  await op('createSearchIndex', async () =>
    coll.createSearchIndex({ name: 'default', definition: { mappings: { dynamic: true } } }),
  );
  const search = () =>
    op('$search', async () => {
      const docs = await coll
        .aggregate([{ $search: { index: 'default', text: { query: 'movie', path: 'title' } } }, { $limit: 200 }])
        .toArray();
      return { count: docs.length };
    });
  if (mode === 'wait') {
    const deadline = now() + queryableDeadline;
    for (;;) {
      const state = await op('$listSearchIndexes', () => searchIndexState(coll));
      if (state.some((i) => i.queryable === true)) break;
      if (now() > deadline) throw new Error(`search index did not become queryable within ${queryableDeadline} s`);
      await new Promise((r) => setTimeout(r, 500));
    }
    await search();
  } else {
    await op('$listSearchIndexes', () => searchIndexState(coll));
    try {
      await search();
    } finally {
      await op('$listSearchIndexes', () => searchIndexState(coll));
    }
  }
} catch (e) {
  rc = 1;
} finally {
  await client.close().catch(() => {});
}
emit({ op: 'end', t_end: now(), rc });
process.exit(rc);
