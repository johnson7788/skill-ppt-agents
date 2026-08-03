import {expect, test, Page} from '@playwright/test';
import {readFileSync} from 'fs';
import {fileURLToPath} from 'url';
import {dirname, join} from 'path';

// 官方 A2A 传输：拦 /a2a 回放真机抓取的 Part[]（backend curl 抓的真实 mapper 产物）。
// 每次调用给卡片换唯一 surfaceId（真后端每轮唯一，回放同一 fixture 需避免 Surface 冲突）。
const DIR = dirname(fileURLToPath(import.meta.url));
const load = (name: string) =>
  JSON.parse(readFileSync(join(DIR, 'fixtures', name), 'utf-8')) as Array<Record<string, unknown>>;

// 用 fixture 回放布置路由；返回每次收到的请求体（供断言 contextId 一致 = 同一会话）。
function stub(page: Page, fixture: string) {
  const bodies: Array<{contextId?: string; text?: string; action?: unknown}> = [];
  page.route('**/a2a', async route => {
    bodies.push(JSON.parse(route.request().postData() || '{}'));
    const sid = `card-${bodies.length}-${Math.random().toString(36).slice(2, 8)}`;
    const parts = load(fixture).map(p => {
      if (p.kind === 'data') {
        const d = JSON.parse(JSON.stringify(p.data)) as Record<string, any>;
        if (d.createSurface) d.createSurface.surfaceId = sid;
        if (d.updateComponents) d.updateComponents.surfaceId = sid;
        return {...p, data: d};
      }
      return p;
    });
    await route.fulfill({
      status: 200,
      headers: {'Content-Type': 'text/event-stream'},
      body: `data: ${JSON.stringify(parts)}\n\n`,
    });
  });
  return bodies;
}

test('循证卡渲染：自定义组件全部到位', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));
  stub(page, 'evidence.json');

  await page.goto('/');
  await page.getByText('孕妇能吃氯雷他定吗？').click();

  await expect(page.locator('.ev-header-title')).toHaveText('循证决策支持');
  await expect(page.locator('.ev-badge')).toContainText('级证据');
  await expect(page.locator('.ev-caution').first()).toBeVisible();
  await expect(page.locator('.a2ui-card')).toHaveCount(1);
  await expect(page.getByText('查看原文 →').first()).toBeVisible();
  expect(errors, errors.join('\n')).toHaveLength(0);
});

test('多轮：追问出第二张卡，两轮共用同一 contextId（有状态）', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));
  const bodies = stub(page, 'evidence.json');

  await page.goto('/');
  await page.getByText('孕妇能吃氯雷他定吗？').click();
  await expect(page.locator('.a2ui-card')).toHaveCount(1);
  await expect(page.locator('.bubble.user')).toHaveCount(1);

  await page.fill('.composer input', '那哺乳期呢？');
  await page.click('.composer button[type=submit]');
  await expect(page.locator('.bubble.user')).toHaveCount(2);
  await expect(page.locator('.a2ui-card')).toHaveCount(2); // 两张独立卡，无 Surface 冲突

  expect(bodies).toHaveLength(2);
  expect(bodies[0].contextId).toBeTruthy();
  expect(bodies[1].contextId).toBe(bodies[0].contextId); // 同一会话 = 后端复用 InMemorySession
  const surfErr = errors.filter(e => /already exists|Surface/i.test(e));
  expect(surfErr, surfErr.join('\n')).toHaveLength(0);
});

test('自测问卷：渲染量表并本地评分出结果', async ({page}) => {
  stub(page, 'questionnaire.json');

  await page.goto('/');
  await page.getByText('测测我最近是不是抑郁了').click();

  await expect(page.locator('.quiz')).toBeVisible();
  const n = await page.locator('.quiz-item').count();
  expect(n).toBeGreaterThan(0);
  await expect(page.locator('.quiz-submit')).toBeDisabled();

  for (let i = 0; i < n; i++) {
    await page.locator('.quiz-item').nth(i).locator('.quiz-opt input').first().check();
  }
  await expect(page.locator('.quiz-submit')).toBeEnabled();
  await page.locator('.quiz-submit').click();
  await expect(page.locator('.quiz-result')).toBeVisible();
  await expect(page.locator('.quiz-score')).toContainText('总分');
});
