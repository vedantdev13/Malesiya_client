import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Listen on all interfaces so any device on the same LAN can connect
    host: '0.0.0.0',
    proxy: {
      // Proxy all /api requests → FastAPI backend (works from any device via LAN)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
      // Proxy WebSocket /ws → FastAPI backend
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
});
