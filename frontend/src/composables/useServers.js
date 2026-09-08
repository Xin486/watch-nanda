/**
 * 服务器数据实时推送 composable
 * --------------------------------
 * 双通道保障，数据永远不断：
 *   1. SSE 主通道：后端每 10 秒推送，连接成功时实时更新
 *   2. REST 兜底：每 15 秒轮询一次，SSE 断开时保底
 *   3. 首次订阅立即 REST 拉取，页面秒开
 */
import { ref, onUnmounted } from 'vue'
import axios from 'axios'

// ── 模块级单例状态 ─────────────────────────────────────────
let eventSource = null
let subscribers = 0
let fallbackTimer = null
let restFallbackActive = false
const servers = ref([])
let initialized = false

const BASE = `http://${window.location.hostname}:7980`

// ── REST 拉取 ─────────────────────────────────────────────
function fetchOnce () {
  axios.get(`${BASE}/api/servers`).then(res => {
    servers.value = res.data
  }).catch(() => {})
}

// ── REST 兜底轮询（SSE 死了也不怕）────────────────────────
function startFallback () {
  if (fallbackTimer) return
  restFallbackActive = true
  fallbackTimer = setInterval(() => {
    fetchOnce()
  }, 15000)
}

function stopFallback () {
  if (fallbackTimer) {
    clearInterval(fallbackTimer)
    fallbackTimer = null
  }
  restFallbackActive = false
}

// ── SSE 主通道 ────────────────────────────────────────────
function openSSE () {
  if (eventSource) return
  eventSource = new EventSource(`${BASE}/api/stream/servers`)

  eventSource.onmessage = (e) => {
    try {
      servers.value = JSON.parse(e.data)
      // SSE 工作正常，停掉兜底轮询
      stopFallback()
    } catch { /* 忽略解析错误 */ }
  }

  eventSource.onerror = () => {
    // 不手动 close：让浏览器自动重连
    // 启动兜底轮询，重连期间数据不中断
    startFallback()
  }
}

function closeSSE () {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

// ── CRUD 刷新（只绑定一次）────────────────────────────────
let refreshBound = false
function bindRefresh () {
  if (refreshBound) return
  refreshBound = true
  window.addEventListener('servers-refresh', () => fetchOnce())
}

// ── 组合式函数 ────────────────────────────────────────────
export function useServers () {
  const subscribe = () => {
    subscribers++

    // 首次订阅：立即拉一次 + 启动兜底 + 建立 SSE
    if (!initialized) {
      initialized = true
      fetchOnce()           // 立即出数据
      startFallback()       // 兜底轮询先开着
      openSSE()             // SSE 连上后会自动停掉兜底
      bindRefresh()         // CRUD 后立即刷新
    }
  }

  onUnmounted(() => {
    subscribers--
    if (subscribers <= 0) {
      subscribers = 0
      initialized = false
      stopFallback()
      closeSSE()
    }
  })

  return { servers, subscribe }
}
