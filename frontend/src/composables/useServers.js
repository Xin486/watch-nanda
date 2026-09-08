/**
 * 服务器数据实时推送 composable
 * --------------------------------
 * 用 Server-Sent Events 替代原来的 60 秒轮询：
 *   - 后端每 10 秒推送一次最新数据（只读数据库缓存，不触发 SSH 采集）
 *   - 单例 EventSource：多个组件共享同一条连接，最后一个组件卸载时自动关闭
 *   - 组件卸载时自动退订，无内存泄漏
 */
import { ref, onUnmounted } from 'vue'

// ── 模块级单例 ──────────────────────────────────────────────
let eventSource = null        // 单例 SSE 连接
let subscribers = 0           // 当前订阅计数
const servers = ref([])       // 共享的服务器数据（所有组件看到的是同一个 ref）

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
    // 连接断开 → 关闭，下次 subscribe 时重新打开
    eventSource.close()
    eventSource = null
  }
}

function close () {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

// ── 组合式函数（每个调用的组件会自动订阅/退订）──────────────
export function useServers () {
  const subscribe = () => {
    subscribers++
    open()
  }

  onUnmounted(() => {
    subscribers--
    if (subscribers <= 0) {
      subscribers = 0
      close()
    }
  })

  return { servers, subscribe }
}
