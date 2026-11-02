import { createInterface } from 'node:readline';
import { MongoClient } from 'mongodb';

const uri = 'mongodb://localhost:27017/?directConnection=true';
const now = () => Date.now() / 1000;
const emit = (o) => process.stdout.write(JSON.stringify(o) + '\n');

const client = new MongoClient(uri);
emit({ ev: 'connect-start', t: now() });
await client.connect();
await client.db('admin').command({ ping: 1 });
emit({ ev: 'ready', t: now() });
const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (line.trim() !== 'go') continue;
  const t_start = now();
  try {
    const list = await client.db('test').collection('items').listIndexes().toArray();
    emit({ ev: 'listIndexes', t_start, t_end: now(), ok: true, indexes: list.map((i) => ({ name: i.name, key: i.key })) });
  } catch (e) {
    emit({ ev: 'listIndexes', t_start, t_end: now(), ok: false, error: { class: e?.constructor?.name, message: e?.message, code: e?.code, codeName: e?.codeName } });
  }
  break;
}
rl.close();
await client.close();
