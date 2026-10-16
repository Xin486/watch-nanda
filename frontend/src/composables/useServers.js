import { ref, computed, onUnmounted } from 'vue'
import axios from 'axios'
import { freshServer } from '../utils/serverStatus'

const rawServers = ref([])
const elapsed = ref(0)
const servers = computed(() => rawServers.value.map(s => freshServer(s, elapsed.value)))
const BASE = window.location.protocol === 'https:' ? window.location.origin : `http://${window.location.hostname}:7980`
let source = null, subscribers = 0, timer = null, fallbackTimer = null
let controller = null, receivedAt = 0, lastEventAt = 0, generation = 0, revision = 0

function accept(data) {
  if (!Array.isArray(data)) return
  rawServers.value = data
  receivedAt = performance.now()
  elapsed.value = 0
  revision++
}

async function fetchOnce() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  const currentGeneration = generation, currentRevision = revision
  try {
    const { data } = await axios.get(`${BASE}/api/servers`, { signal: request.signal, timeout: 12000 })
    if (!request.signal.aborted && currentGeneration === generation && currentRevision === revision) accept(data)
  } catch { /* 保留快照，计时器仍会将其标记为过期。 */ }
}

function start() {
  generation++
  lastEventAt = 0
  fetchOnce()
  source = new EventSource(`${BASE}/api/stream/servers`)
  source.onmessage = event => {
    try {
      const data = JSON.parse(event.data)
      if (!Array.isArray(data)) return
      lastEventAt = performance.now()
      accept(data)
    } catch { /* 下一次推送或 REST 兜底重试。 */ }
  }
  source.onerror = () => { lastEventAt = 0 }
  timer = setInterval(() => { elapsed.value = receivedAt ? (performance.now() - receivedAt) / 1000 : 0 }, 1000)
  fallbackTimer = setInterval(() => {
    if (!lastEventAt || performance.now() - lastEventAt > 25000) fetchOnce()
  }, 15000)
  window.addEventListener('servers-refresh', fetchOnce)
}

function stop() {
  generation++
  controller?.abort()
  source?.close()
  source = null
  clearInterval(timer)
  clearInterval(fallbackTimer)
  window.removeEventListener('servers-refresh', fetchOnce)
}

export function useServers() {
  let subscribed = false
  const subscribe = () => {
    if (subscribed) return
    subscribed = true
    if (++subscribers === 1) start()
  }
  onUnmounted(() => {
    if (subscribed && --subscribers === 0) stop()
  })
  return { servers, subscribe }
}
