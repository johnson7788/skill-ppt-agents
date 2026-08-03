import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {plugin as a2a} from './middleware/a2a';

export default defineConfig({
  plugins: [react(), a2a()],
  server: {
    port: 5273,
  },
});
