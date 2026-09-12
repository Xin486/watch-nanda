export const statusLabel = status => ({ online: '在线', offline: '离线', stale: '数据过期', pending: '待采集' }[status] || '未知')

export function freshServer(server, elapsedSeconds = 0) {
  const age = server.data_age_seconds == null ? null : server.data_age_seconds + Math.max(0, elapsedSeconds)
  const status = age == null ? 'pending' : age > (server.stale_after_seconds || 180) ? 'stale' : server.collection_status
  return { ...server, status, data_age_seconds: age }
}

export function ageLabel(seconds) {
  if (seconds == null) return '等待首次采集'
  if (seconds < 60) return `${Math.floor(seconds)} 秒前采集`
  return `${Math.floor(seconds / 60)} 分钟前采集`
}
