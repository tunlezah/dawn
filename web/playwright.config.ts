import { defineConfig } from '@playwright/test';

// Smoke tests run against the simulator stack: `make sim` (or scripts/simctl.sh start) in another terminal.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  // every test drives the one simulator stack (and resets it in beforeEach), so projects must not overlap
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.DAWN_URL || 'http://127.0.0.1:8080',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'face', use: { viewport: { width: 800, height: 480 }, hasTouch: true }, testMatch: /face\.spec\.ts/ },
    { name: 'control-mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, testMatch: /control\.spec\.ts/ },
  ],
});
