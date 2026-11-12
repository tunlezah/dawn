// When an open page may load a new build of the web UI (src/shared/store.ts), without a browser.
import { test, expect } from '@playwright/test';
import { shouldReload } from '../src/shared/store';

const el = (tagName: string) => ({ tagName }) as unknown as Element;

test('a page whose build was replaced reloads, but not into a ring or what is being typed', () => {
  expect(shouldReload(false, null)).toBe(true);
  expect(shouldReload(false, el('BODY'))).toBe(true);
  expect(shouldReload(true, null)).toBe(false); // the face beeps along with the backup tone: a reload would cut it
  for (const tag of ['INPUT', 'TEXTAREA', 'SELECT']) expect(shouldReload(false, el(tag))).toBe(false);
});
