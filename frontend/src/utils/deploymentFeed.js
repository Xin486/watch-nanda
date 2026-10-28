/** One read-only subscription with REST fallback; reconnect never submits commands. */
export function createDeploymentFeed({ url, fetchSnapshot, onSnapshot, onState, onError, isComplete,
  EventSourceImpl = globalThis.EventSource, now = () => Date.now(),
  every = globalThis.setInterval, cancel = globalThis.clearInterval }) {
  let closed = false, source, timer, controller, fetching = false, revision = 0, lastEvent = now()
  const state = value => { if (!closed) onState(value) }
  function stop() {
    closed = true
    source?.close()
    cancel(timer)
    controller?.abort()
  }
  function accept(data, mode) {
    revision++
    state(isComplete(data) ? 'complete' : mode)
    onSnapshot(data)
    if (isComplete(data)) stop()
  }
  function fail(error) {
    state('error')
    stop()
    onError(error)
  }
  async function refresh() {
    if (closed || fetching) return
    fetching = true
    controller = new AbortController()
    const current = revision
    try {
      const { data } = await fetchSnapshot({ signal: controller.signal })
      // A newer push always wins over an older HTTP response.
      if (!closed && current === revision) accept(data, 'fallback')
    } catch (error) {
      if (!closed && current === revision) {
        if ([401, 403, 404].includes(error.response?.status)) fail(error)
        else state('reconnecting')
      }
    } finally { fetching = false }
  }
  state('connecting')
  timer = every(() => {
    if (!source || now() - lastEvent > 20000) { state('fallback'); refresh() }
  }, 5000)
  try {
    source = new EventSourceImpl(url)
    source.addEventListener('snapshot', event => {
      if (closed) return
      try { const data = JSON.parse(event.data); lastEvent = now(); accept(data, 'live') }
      catch { state('fallback'); refresh() }
    })
    source.addEventListener('heartbeat', () => { if (!closed) { lastEvent = now(); state('live') } })
    source.addEventListener('feed-error', event => {
      if (closed) return
      try { const data = JSON.parse(event.data); fail({ response: { status: data.status, data: { detail: data.detail } } }) }
      catch { fail({ response: { status: 503, data: { detail: '进度连接异常，请重新打开任务' } } }) }
    })
    source.onerror = () => { if (!closed) { lastEvent = 0; state('reconnecting'); refresh() } }
  } catch { source = null; state('fallback'); refresh() }
  return { stop, refresh }
}
