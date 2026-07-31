import {defineConfig, devices} from '@playwright/test';

// 本机 http_proxy 会拦 localhost（curl 需 --noproxy），会让 playwright 的 readiness
// 探测与浏览器加载 localhost 走代理失败。e2e 全是 localhost/mock，直接清掉代理。
for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']) {
  delete process.env[k];
}
process.env.NO_PROXY = process.env.no_proxy = 'localhost,127.0.0.1';

// evidence-a2ui 端到端：起 mock 后端(:8700, EVIDENCE_MOCK=1 确定性返回 fixture，不触网/LLM)
// + vite(:5273)，浏览器验证 React 渲染、追问增量、Vanilla 多端渲染。
export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  fullyParallel: false,
  reporter: [['line']],
  use: {baseURL: 'http://localhost:5273', ...devices['Desktop Chrome']},
  webServer: [
    {
      command: 'EVIDENCE_MOCK=1 backend/.venv/bin/python backend/server_evidence.py',
      cwd: '../../..', // repo 根（config 在 frontend/web/e2e/）
      url: 'http://localhost:8700/',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npx vite --port 5273',
      cwd: '..', // web/
      url: 'http://localhost:5273/',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
