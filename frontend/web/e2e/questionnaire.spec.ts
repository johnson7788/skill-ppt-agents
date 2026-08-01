import {expect, test} from '@playwright/test';

// 后端 EVIDENCE_MOCK=1：含问卷触发词的问题返回 phq9.json 量表 fixture（不触网/LLM）。
// 测的是问卷模式渲染 + 前端本地评分（选项 score 求和 → 落 band），非路由 LLM。
test('自测问卷：渲染量表并本地评分出结果', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(String(e)));

  await page.goto('/');
  await page.fill('.composer input', '测测我最近是不是抑郁了');
  await page.click('.composer button');

  await expect(page.locator('.quiz')).toBeVisible({timeout: 30_000});
  const items = page.locator('.quiz-item');
  const n = await items.count();
  expect(n).toBeGreaterThan(0);

  // 未答完时提交禁用
  await expect(page.locator('.quiz-submit')).toBeDisabled();

  // 每题选最后一个选项（PHQ-9 = 「几乎每天」score 3）→ 总分 3n
  for (let i = 0; i < n; i++) {
    await items.nth(i).locator('.quiz-opt').last().click();
  }
  await expect(page.locator('.quiz-submit')).toBeEnabled();
  await page.click('.quiz-submit');

  await expect(page.locator('.quiz-result')).toBeVisible();
  await expect(page.locator('.quiz-score')).toContainText(`总分 ${n * 3}`);
  await expect(page.locator('.quiz-advice')).toBeVisible();
  await expect(page.locator('.quiz-disclaimer')).toContainText('仅供参考');
  expect(errors, errors.join('\n')).toHaveLength(0);
});
