import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': { target: process.env.RELAY_DEV_BACKEND || 'http://127.0.0.1:8000' },
    },
  },
});
