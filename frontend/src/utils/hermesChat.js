import { secureOrigin } from './transport'

const base = '/api/ops/hermes/conversations'
export function hermesBackgroundNotice(previous, next, open, current = '') {
  if (open || !next.id) return ''
  if (next.status === 'running') return '任务正在后台执行'
  if (previous.status === 'running') {
    if (next.status === 'idle') return '任务已完成，点击查看'
    if (next.status === 'stopped') return '任务已停止，点击查看'
    return '任务需要关注，点击查看'
  }
  return current
}

export async function hermesRequest(path = '', { method = 'GET', body, signal } = {}) {
  const response = await fetch(secureOrigin() + base + path, {
    method, signal, credentials: 'same-origin', cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '聊天服务暂不可用，请稍后重试。')
    error.status = response.status
    throw error
  }
  return data
}

// One owner per instance; stale responses can never replace a newer conversation.
export function createHermesChat({ request = hermesRequest, changed, every = setTimeout, cancel = clearTimeout }) {
  let epoch = 0, timer, pending, syncing, active = true, disposed = false
  let state = { id: '', messages: [], status: 'idle', error: '', activity: '' }
  const emit = () => changed({ ...state, messages: state.messages.map(m => ({ ...m })) })
  const stopTimer = () => { cancel(timer); timer = undefined }
  function failure(error) {
    if (error.name === 'AbortError') return
    if ([401, 404].includes(error.status)) {
      state = { id: '', messages: [], status: 'idle', error: error.message, activity: '' }
    } else state.error = error.message || '连接中断，正在同步对话…'
    emit()
  }
  function schedule() {
    stopTimer()
    if (!disposed && state.id && (active || state.status === 'running')) {
      timer = every(() => sync(), state.status === 'running' ? 800 : 10000)
    }
  }
  async function sync() {
    if (!state.id || disposed || pending || syncing) return
    const current = epoch
    const controller = new AbortController()
    syncing = controller
    try {
      const next = await request('/' + state.id, { signal: controller.signal })
      if (current === epoch && !disposed) { state = next; emit() }
    } catch (error) { if (current === epoch && !disposed) failure(error) }
    finally { if (syncing === controller) syncing = undefined; if (current === epoch) schedule() }
  }
  async function mutate(action) {
    if (pending || disposed) return
    const current = ++epoch
    syncing?.abort(); syncing = undefined
    const controller = new AbortController()
    pending = controller
    stopTimer()
    try {
      const next = await action(controller.signal, () => current === epoch && !disposed)
      if (current === epoch && !disposed) { state = next; emit() }
    } catch (error) { if (current === epoch && !disposed) failure(error) }
    finally { if (pending === controller) pending = undefined; if (current === epoch) schedule() }
  }
  return {
    restore(id) { if (id && /^[A-Za-z0-9_-]{16,80}$/.test(id)) { state.id = id; return sync() } },
    setActive(value) { active = value; if (value) return sync(); schedule() },
    send(text) {
      if (!text.trim() || state.status === 'running') return
      return mutate(async (signal, current) => {
        if (!state.id) {
          const created = await request('', { method: 'POST', signal })
          if (!current()) return created
          state = created; emit()
        }
        return request('/' + state.id + '/messages', { method: 'POST', signal,
          body: { text, request_id: crypto.randomUUID() } })
      })
    },
    stop() { return mutate(signal => request('/' + state.id + '/stop', { method: 'POST', signal })) },
    async reset() {
      if (pending || disposed) return
      const id = state.id
      await mutate(async signal => {
        if (id) await request('/' + id, { method: 'DELETE', signal })
        return { id: '', messages: [], status: 'idle', error: '', activity: '' }
      })
    },
    clear() {
      epoch++; pending?.abort(); pending = undefined; syncing?.abort(); syncing = undefined; stopTimer()
      state = { id: '', messages: [], status: 'idle', error: '', activity: '' }; emit()
    },
    dispose() { disposed = true; epoch++; pending?.abort(); syncing?.abort(); stopTimer() },
  }
}
