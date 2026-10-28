import { expect, test } from 'vitest';
import { total } from '../src/total';

test('total uses add from a', () => {
  expect(total([2, 3, 4])).toBe(9);
});
