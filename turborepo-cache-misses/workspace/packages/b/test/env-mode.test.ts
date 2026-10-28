import { expect, test } from 'vitest';

test('APP_MODE', () => {
  const mode = process.env.APP_MODE ?? '';
  console.log(`APP_MODE seen by the test: "${mode}"`);
  expect(mode).not.toBe('new');
});
