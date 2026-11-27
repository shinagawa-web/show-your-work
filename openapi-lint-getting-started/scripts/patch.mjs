import { readFileSync, writeFileSync } from 'node:fs';

const patches = {
  'snake-case-property-in-order': {
    after: '          format: date-time\n',
    insert: '        updated_at:\n          type: string\n          format: date-time\n',
  },
  'string-quantity-example': {
    after: [
      '          description: The item.',
      '          content:',
      '            application/json:',
      '              schema:',
      "                $ref: '#/components/schemas/OrderItem'",
      '',
    ].join('\n'),
    insert: [
      '              example:',
      '                id: 9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d',
      '                productId: 2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e',
      "                quantity: '2'",
      '',
    ].join('\n'),
  },
};

const [file, name] = process.argv.slice(2);
const patch = patches[name];
if (!patch) {
  console.error(`unknown patch: ${name}`);
  process.exit(2);
}
const text = readFileSync(file, 'utf8');
const parts = text.split(patch.after);
if (parts.length !== 2) {
  console.error(`${name}: expected the anchor once in ${file}, found ${parts.length - 1}`);
  process.exit(1);
}
writeFileSync(file, parts[0] + patch.after + patch.insert + parts[1]);
