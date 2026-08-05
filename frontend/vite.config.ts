import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

// 后端地址：本地 dev 默认 8686；docker 部署时后端在 8046，用 BACKEND_URL 覆盖。
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8686';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    port: 3686,
    hmr: process.env.DISABLE_HMR !== 'true',
    watch: process.env.DISABLE_HMR === 'true' ? null : {},
    proxy: Object.fromEntries(
      ['/chat', '/api', '/upload', '/uploads', '/decks', '/download', '/preview', '/preview-static'].map(
        (p) => [p, BACKEND],
      ),
    ),
  },
});
