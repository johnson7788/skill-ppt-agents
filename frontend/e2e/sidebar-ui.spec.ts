import { test, expect } from '@playwright/test';

/**
 * Real browser UI test for the file sidebar. The API-only e2e in
 * ppt-workflow.spec.ts passed while the UI stayed empty because vite's dev
 * proxy was missing a `/decks` entry — so listDecks() hit the SPA, not the
 * backend. This test loads the actual frontend and asserts the sidebar
 * renders backend files and refreshes after an upload.
 */

const FRONTEND = 'http://localhost:3686';

test('sidebar renders backend files and refreshes after upload', async ({ page }) => {
  await page.goto(FRONTEND);

  // "我的文件" sidebar header must be present
  await expect(page.getByText('我的文件')).toBeVisible();

  // a probe file uploaded straight to the backend appears in the list
  const unique = `ui_probe_${Date.now()}.txt`;
  await page.evaluate(async (name) => {
    const fd = new FormData();
    fd.append('user_id', 'default_user');
    fd.append('file', new File(['hi'], name, { type: 'text/plain' }));
    await fetch('/upload', { method: 'POST', body: fd });
  }, unique);

  // click the sidebar refresh (RefreshCw button) then assert the file shows
  await page.getByTitle('刷新').click();
  await expect(page.getByText(unique)).toBeVisible({ timeout: 5000 });

  // clean up
  await page.evaluate(
    (name) =>
      fetch(`/uploads?user_id=default_user&file=${encodeURIComponent(name)}`, { method: 'DELETE' }),
    unique,
  );
});
