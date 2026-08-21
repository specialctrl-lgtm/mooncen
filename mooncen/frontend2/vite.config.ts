import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.VITE_API_PROXY_TARGET || env.VITE_API_BASE_URL || 'http://127.0.0.1:8001';
  const allowedHosts = (env.VITE_ALLOWED_HOSTS || 'localhost,127.0.0.1')
    .split(',')
    .map((host) => host.trim())
    .filter(Boolean);

  return {
    plugins: [react()],
    server: {
      host: env.VITE_DEV_HOST || '127.0.0.1',
      port: Number(env.VITE_DEV_PORT || 5174),
      strictPort: true,
      allowedHosts,
      proxy: {
        '/api': apiTarget,
        '/course': apiTarget,
        '/category': apiTarget,
        '/branch': apiTarget,
        '/health': apiTarget,
      },
    },
    preview: {
      host: env.VITE_PREVIEW_HOST || '127.0.0.1',
      port: Number(env.VITE_PREVIEW_PORT || 4174),
      strictPort: true,
      allowedHosts,
    },
  };
});
