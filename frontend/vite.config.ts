import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

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
    proxy: {
      '/chat': 'http://localhost:8686',
      '/upload': 'http://localhost:8686',
      '/uploads': 'http://localhost:8686',
      '/download': 'http://localhost:8686',
      '/preview': 'http://localhost:8686',
      '/preview-static': 'http://localhost:8686',
    },
  },
});
