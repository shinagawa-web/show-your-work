import { add } from 'a/math';

export function total(xs: number[]): number {
  return xs.reduce((sum, x) => add(sum, x), 0);
}
