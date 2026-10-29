import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';

test('fixture at the repository root', () => {
  const data = JSON.parse(readFileSync(new URL('../../../fixtures/shared.json', import.meta.url), 'utf8'));
  expect(data.status).toBe('ok');
});
