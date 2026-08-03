import {defineConfig, devices} from '@playwright/test';

// 本机 http_proxy 会拦 localhost，清掉避免 readiness 探测/加载失败。
for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']) {
  delete process.env[k];
}
process.env.NO_PROXY = process.env.no_proxy = 'localhost,127.0.0.1';

// 官方 A2A 传输重构后：e2e 用 page.route 拦 /a2a 回放真机抓取的 Part[] fixture
// （确定性、不触网/LLM/后端）。只需起 vite。真实 A2A 链路已由后端 curl + 手工浏览器冒烟验证。
export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  fullyParallel: false,
  reporter: [['line']],
  use: {baseURL: 'http://localhost:5273', ...devices['Desktop Chrome']},
  webServer: [
    {
      command: 'npx vite --port 5273',
      cwd: '..',
      url: 'http://localhost:5273/',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
