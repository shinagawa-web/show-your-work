import mongoose from 'mongoose';

export const uri = 'mongodb://localhost:27017/?directConnection=true';
export const now = () => Date.now() / 1000;
export const emit = (o) => process.stdout.write(JSON.stringify(o) + '\n');

export function describe(e) {
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

process.on('unhandledRejection', (reason) => {
  emit({ ev: 'unhandledRejection', t: now(), error: describe(reason) });
  throw reason;
});
process.on('uncaughtExceptionMonitor', (err, origin) => {
  emit({ ev: 'uncaughtException', t: now(), origin, error: describe(err) });
});
process.on('exit', (code) => emit({ ev: 'exit', t: now(), code }));

if (process.env.MONGOOSE_DEBUG === '1') {
  mongoose.set('debug', (collection, method, ...args) => {
    let text;
    try {
      text = JSON.stringify(args);
    } catch {
      text = String(args);
    }
    emit({ ev: 'debug', t: now(), collection, method, args: text });
  });
}

const schema = new mongoose.Schema({ name: { type: String, index: true } });
schema.pre('createCollection', function () {
  emit({ ev: 'createCollection-start', t: now() });
});
schema.post('createCollection', function () {
  emit({ ev: 'createCollection-end', t: now() });
});

export const Item = mongoose.model('Item', schema);

Item.on('index-single-start', (fields, options) => emit({ ev: 'createIndex-start', t: now(), fields, options }));
Item.on('index-single-done', (err, fields, options, name) =>
  emit({ ev: 'createIndex-done', t: now(), ok: !err, name, error: err ? describe(err) : undefined }),
);
Item.on('index', (err) => emit({ ev: 'model-index', t: now(), ok: !err, error: err ? describe(err) : undefined }));
Item.on('error', (err) => emit({ ev: 'model-error', t: now(), error: describe(err) }));
Item.init().then(
  () => emit({ ev: 'init-resolved', t: now() }),
  (err) => emit({ ev: 'init-rejected', t: now(), error: describe(err) }),
);
for (const e of ['connected', 'disconnected', 'close', 'error', 'reconnected']) {
  mongoose.connection.on(e, (x) => emit({ ev: 'connection-' + e, t: now(), error: e === 'error' ? describe(x) : undefined }));
}

export { mongoose };
