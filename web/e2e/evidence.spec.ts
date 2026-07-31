import {expect, test} from '@playwright/test';

// 后端 EVIDENCE_MOCK=1，两轮都返回同一 fixture（氯雷他定 A 级卡）。
// 这里测的是渲染管线与 A2UI 增量/多端机制，非 LLM（真实检索已手工验证）。

test('React 端渲染循证卡（全部自定义组件到位）', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));

  await page.goto('/');
  await expect(page.locator('.ev-header')).toBeVisible({timeout: 30_000});
  await expect(page.locator('.ev-header-title')).toHaveText('循证决策支持');
  await expect(page.locator('.ev-badge')).toContainText('级证据');
  await expect(page.locator('.ev-caution')).toHaveCount(1);
  await expect(page.locator('.ev-caution-hl').first()).toBeVisible();
  await expect(page.locator('.a2ui-card')).toHaveCount(1);
  await expect(page.getByText('查看原文').first()).toBeVisible();
  expect(errors, errors.join('\n')).toHaveLength(0);
});

test('追问增量更新：同一张卡不重建、无 Surface 冲突', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));

  await page.goto('/');
  await expect(page.locator('.ev-header')).toBeVisible({timeout: 30_000});
  await expect(page.locator('.a2ui-card')).toHaveCount(1);
  await expect(page.locator('.bubble.user')).toHaveCount(1);

  await page.fill('.composer input', '那孕妇能吃吗？');
  await page.click('.composer button');

  // 第二个 user 气泡出现 = 追问已发出
  await expect(page.locator('.bubble.user')).toHaveCount(2);
  // 关键：卡片仍然只有一张（surface 未删除重建），无 "Surface already exists"
  await expect(page.locator('.ev-header')).toBeVisible();
  await expect(page.locator('.a2ui-card')).toHaveCount(1);
  const surfaceErr = errors.filter(e => /already exists|Surface/i.test(e));
  expect(surfaceErr, surfaceErr.join('\n')).toHaveLength(0);
});

test('Vanilla 多端渲染：同一 /a2a 消息被非 React 客户端渲染', async ({page}) => {
  await page.goto('/vanilla.html');
  // 手写 vanilla 渲染器解释同一份 A2UI 组件树
  await expect(page.locator('.ev-header-title')).toHaveText('循证决策支持', {timeout: 30_000});
  await expect(page.locator('.ev-badge')).toContainText('级证据');
  await expect(page.locator('.ev-caution')).toHaveCount(1);
  await expect(page.locator('.v-card')).toHaveCount(1);
  await expect(page.getByText('查看原文').first()).toBeVisible();
});
