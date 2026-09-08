/**
 * 服务器数据实时推送 composable
 * --------------------------------
 * 用 Server-Sent Events 替代原来的 60 秒轮询：
 *   - 订阅时立即 REST 拉一次，页面秒开
 *   - 后端每 10 秒通过 SSE 推送最新数据（只读数据库缓存，不触发 SSH 采集）
 *   - 单例 EventSource：多个组件共享同一条连接，最后一个组件卸载时自动关闭
 *   - 组件卸载时自动退订，无内存泄漏
 */
import { ref, onUnmounted } from 'vue'
import axios from 'axios'

// ── 模块级单例 ──────────────────────────────────────────────
let eventSource = null
let subscribers = 0
const servers = ref([])
let fetched = false  // 是否已做过首次 REST 拉取

const BASE = `http://${window.location.hostname}:7980`

function open () {
  if (eventSource) return
  eventSource = new EventSource(`${BASE}/api/stream/servers`)

  eventSource.onmessage = (e) => {
    try {
      servers.value = JSON.parse(e.data)
    } catch { /* 忽略解析错误 */ }
  }

  eventSource.onerror = () => {
    eventSource.close()
    eventSource = null
  }

  // 任何页面执行 CRUD 后 dispatch 此事件，立即拉取最新数据
  window.addEventListener('servers-refresh', () => {
    axios.get(`${BASE}/api/servers`).then(res => {
      servers.value = res.data
    }).catch(() => {})
  })
}

function close () {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

// ── 组合式函数 ─────────────────────────────────────────────
export function useServers () {
  const subscribe = () => {
    subscribers++

    // 首次订阅：立即 REST 拉一次，页面秒开不等 SSE
    if (!fetched) {
      fetched = true
      axios.get(`${BASE}/api/servers`).then(res => {
        servers.value = res.data
      }).catch(() => {})
    }

    open()
  }

  onUnmounted(() => {
    subscribers--
    if (subscribers <= 0) {
      subscribers = 0
      fetched = false
      close()
    }
  })

  return { servers, subscribe }
}
