import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'

export default defineConfig(({ command }) => {
  const https = command === 'serve' ? {
    cert: readFileSync(process.env.WATCH_NANDA_TLS_CERT || '/etc/node-monitor/tls/server.crt'),
    key: readFileSync(process.env.WATCH_NANDA_TLS_KEY || '/etc/node-monitor/tls/server.key'),
  } : undefined
  const proxy = {
    '/api': { target: `http://127.0.0.1:${process.env.WATCH_NANDA_BACKEND_PORT || 7980}`, ws: true, headers: { 'X-Forwarded-Proto': 'https' } },
  }
  const bindHost = process.env.WATCH_NANDA_HTTPS_HOST?.includes(':') ? '::' : '0.0.0.0'
  return {
  plugins: [vue()],
  server: {
    port: Number(process.env.WATCH_NANDA_FRONTEND_PORT || 3000),
    host: bindHost,
    strictPort: true,
    open: false,
    https,
    proxy,
  },
  preview: { host: bindHost, port: Number(process.env.WATCH_NANDA_FRONTEND_PORT || 3000), strictPort: true, https, proxy },
  }
})
