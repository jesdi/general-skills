/**
 * pr-visual-diff capture spec — TEMPORARY SCAFFOLDING, never commit.
 *
 * This is a template. Before running, substitute every __PVD_*__ marker below
 * with the value from this project's discovery cache
 * (skills/pr-visual-diff/pr-visual-diff.<slug>.json). The markers exist because
 * each project wires its Playwright auth fixture and API mock differently.
 *
 * Copy the filled result to <specsDir>/__pr-capture__.spec.ts, then run:
 *
 *   PR_SHOT_DIR=<abs-out-dir> PR_SHOT_LABEL=after \
 *     <packageManager> exec playwright test <specsDir>/__pr-capture__.spec.ts \
 *     --project=<deviceProject> [--project=<deviceProject>...]
 *
 * Delete the copy when done — anything left in the specs dir runs on every e2e pass.
 */
import { expect, type Page } from '@playwright/test';
// __PVD_AUTH_IMPORT__: import the project's test fixture.
// From config: import { <authFixtureName> } from '<authFixtureImport>'
import { test } from '__PVD_AUTH_FIXTURE_IMPORT__';
// __PVD_MOCK_IMPORT__: import the project's API-mock helper.
// From config: import { <mockHelperName> } from '<mockHelperImport>'
import { mockApi } from '__PVD_MOCK_HELPER_IMPORT__';

const OUT = process.env.PR_SHOT_DIR;
const LABEL = process.env.PR_SHOT_LABEL ?? 'after';
if (!OUT) throw new Error('PR_SHOT_DIR must be set to an absolute output directory');

// Selector for this app's loading indicator. From config: <spinnerSelector>.
const SPINNER = '__PVD_SPINNER_SELECTOR__';

// Pin device-pixel-ratio to 1 for every project. Device descriptors (iPhone 14,
// Pixel 7, iPad) default to DPR 2-3, producing PNGs 4-9x larger for zero review
// value — and those bytes land in the main context when the shots get read back.
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
  // __PVD_PAGE_PROP__: destructure the project's authed-page fixture prop.
  // From config: { <authedPageProp>: page }
  test(`pr-capture: ${view.name}`, async ({ authenticatedPage: page }, testInfo) => {
    await mockApi(page);
    await page.goto(view.path);
    await page.waitForLoadState('networkidle');
    await view.prepare?.(page);
    // Guard against shipping a misleading shot: a spinner still on screen means
    // the mock is missing an endpoint or prepare() raced the render. Fail loudly
    // here instead of committing a loading-state PNG.
    await expect(
      page.locator(SPINNER),
      `${view.name}: a spinner is still visible — fix the mock/prepare before capturing`,
    ).toHaveCount(0);
    await page.screenshot({
      path: `${OUT}/${view.name}--${testInfo.project.name}--${LABEL}.png`,
      fullPage: view.fullPage ?? true,
      animations: 'disabled',
      caret: 'hide',
    });
  });
}
