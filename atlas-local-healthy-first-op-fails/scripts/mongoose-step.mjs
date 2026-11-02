import { Item, mongoose, uri, now, emit, describe } from './model.mjs';

const step = process.argv[2];
if (step !== 'seed' && step !== 'test') {
  console.error('usage: node scripts/mongoose-step.mjs seed|test');
  process.exit(2);
}

emit({ ev: 'step', step, t: now(), mongoose: mongoose.version, debug: process.env.MONGOOSE_DEBUG === '1' });

async function op(name, fn) {
  const t_start = now();
  try {
    const result = await fn();
    emit({ ev: 'op', op: name, t_start, t_end: now(), ok: true, result });
    return result;
  } catch (e) {
    emit({ ev: 'op', op: name, t_start, t_end: now(), ok: false, error: describe(e) });
    throw e;
  }
}

await op('connect', async () => {
  await mongoose.connect(uri);
  return null;
});
if (step === 'seed') {
  for (let i = 1; i <= 10; i++) {
    await op(`create${i}`, async () => {
      const doc = await Item.create({ name: `item ${i}` });
      return { _id: String(doc._id) };
    });
  }
} else {
  await op('find', async () => {
    const docs = await Item.find();
    return { count: docs.length };
  });
}
await op('disconnect', async () => {
  await mongoose.disconnect();
  return null;
});
