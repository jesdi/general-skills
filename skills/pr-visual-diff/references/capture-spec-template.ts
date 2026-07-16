/**
 * pr-visual-diff capture spec — TEMPORARY SCAFFOLDING, never commit.
 *
 * Copy to frontend/e2e/specs/__pr-capture__.spec.ts, fill in VIEWS, run:
 *
 *   PR_SHOT_DIR=<abs-out-dir> PR_SHOT_LABEL=after \
 *     pnpm exec playwright test e2e/specs/__pr-capture__.spec.ts \
 *     --project=desktop-chromium --project=mobile-iphone
 *
 * Delete the copy when done — anything left in specs/ runs on every e2e pass.
 */
import { expect, type Page } from '@playwright/test';
import { test } from '../support/test';
import { mockApi } from '../support/mock-api';

const OUT = process.env.PR_SHOT_DIR;
const LABEL = process.env.PR_SHOT_LABEL ?? 'after';
if (!OUT) throw new Error('PR_SHOT_DIR must be set to an absolute output directory');

// Pin device-pixel-ratio to 1 for every project. Device descriptors (iPhone 14,
// Pixel 7, iPad) default to DPR 2-3, producing PNGs 4-9x larger for zero review
// value — and those bytes land in the main context when the shots get read back.
// Overrides the context option the `authenticatedPage` fixture inherits.
test.use({ deviceScaleFactor: 1 });

interface View {
  /** Slug used in the filename, e.g. 'dashboard' or 'add-position-modal'. */
  name: string;
  /** Route to open, e.g. '/dashboard'. */
  path: string;
  /** Optional interactions to reach the state (open a modal, pick a tab…). */
  prepare?: (page: Page) => Promise<void>;
  /** Full-page scroll capture (default) vs just the viewport. */
  fullPage?: boolean;
}

const VIEWS: View[] = [
  // { name: 'dashboard', path: '/dashboard' },
  // {
  //   name: 'add-position-modal',
  //   path: '/',
  //   prepare: async (page) => {
  //     await page.getByRole('button', { name: /add symbol/i }).click();
  //     await page.getByRole('dialog').waitFor();
  //   },
  // },
];

for (const view of VIEWS) {
  test(`pr-capture: ${view.name}`, async ({ authenticatedPage: page }, testInfo) => {
    await mockApi(page);
    await page.goto(view.path);
    await page.waitForLoadState('networkidle');
    await view.prepare?.(page);
    // Guard against shipping a misleading shot: a spinner still on screen means
    // the mock is missing an endpoint or prepare() raced the render. Fail loudly
    // here instead of committing a loading-state PNG. Adjust the selector to
    // whatever this app uses for its loading indicator.
    await expect(
      page.locator('[role="status"], [aria-busy="true"], .animate-spin'),
      `${view.name}: a spinner is still visible — fix the mock/prepare before capturing`,
    ).toHaveCount(0);
    // Freeze animations/carets so before/after diffs show code changes only.
    await page.screenshot({
      path: `${OUT}/${view.name}--${testInfo.project.name}--${LABEL}.png`,
      fullPage: view.fullPage ?? true,
      animations: 'disabled',
      caret: 'hide',
    });
  });
}
