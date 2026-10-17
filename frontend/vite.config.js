import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'

export default defineConfig(({ command }) => {
  const https = command === 'serve' ? {
    cert: readFileSync(process.env.WATCH_NANDA_TLS_CERT || '/etc/node-monitor/tls/server.crt'),
    key: readFileSync(process.env.WATCH_NANDA_TLS_KEY || '/etc/node-monitor/tls/server.key'),
  } : undefined
  const proxy = { '/api': { target: 'http://127.0.0.1:7980', ws: true, headers: { 'X-Forwarded-Proto': 'https' } } }
  return {
  plugins: [vue()],
  server: {
    port: 3000,
    host: '0.0.0.0', // 允许通过服务器 IP 外部访问
    strictPort: true,
    open: false,
    https,
    proxy,
  },
  preview: { host: '0.0.0.0', port: 3000, strictPort: true, https, proxy },
  }
})
