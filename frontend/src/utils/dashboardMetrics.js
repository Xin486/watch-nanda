const number = value => value != null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null
const percent = value => value == null ? null : Math.min(100, Math.max(0, value))
const display = value => value == null ? '—' : Number(value.toFixed(1)).toString()
const color = (value, base) => value >= 85 ? 'var(--danger)' : value >= 60 ? 'var(--warning)' : base

export function gpuMemoryAverage(gpus = []) {
  if (!gpus?.length || gpus.some(g => number(g.memory_percent) == null)) return null
  return gpus.reduce((sum, g) => sum + number(g.memory_percent), 0) / gpus.length
}

export function nodeMetrics(server) {
  const gpus = server.gpu_data || [], cpu = number(server.cpu_percent)
  const memory = number(server.ram_total_mb) > 0 ? number(server.ram_percent) : null
  const gpu = gpuMemoryAverage(gpus)
  const power = gpus.length && gpus.every(g => number(g.power_draw) != null) ? gpus.reduce((sum, g) => sum + number(g.power_draw), 0) : null
  const limit = gpus.length && gpus.every(g => number(g.power_limit) > 0) ? gpus.reduce((sum, g) => sum + number(g.power_limit), 0) : null
  return [
    { label: 'CPU', value: display(cpu), unit: '%', percent: percent(cpu), color: color(cpu, 'var(--metric-cpu)'), note: server.cpu_cores ? `${server.cpu_cores} 个逻辑核心` : '核心数未上报' },
    { label: '内存', value: display(memory), unit: '%', percent: percent(memory), color: color(memory, 'var(--metric-memory)'), note: memory == null || number(server.ram_used_mb) == null ? '容量数据未上报' : `${Math.round(server.ram_used_mb / 1024)} / ${Math.round(server.ram_total_mb / 1024)} GB` },
    { label: '平均显存', value: display(gpu), unit: '%', percent: percent(gpu), color: color(gpu, 'var(--metric-gpu)'), note: gpus.length ? `${gpus.length} 张显卡平均值` : '未检测到显卡数据' },
    { label: 'GPU 总功率', value: power == null ? '—' : String(Math.round(power)), unit: 'W', percent: limit && power != null ? percent(power / limit * 100) : null, color: 'var(--metric-power)', note: power == null ? '未上报功率数据' : limit ? `总功率上限 ${Math.round(limit)} W` : '所有显卡功率之和' },
  ]
}

export function gpuSummary(gpus = []) {
  if (!gpus?.length) return '未检测到 NVIDIA 显卡数据'
  const models = new Map()
  for (const gpu of gpus) { const name = (gpu.model || '未知型号').replace(/^NVIDIA\s+/, ''); models.set(name, (models.get(name) || 0) + 1) }
  return [...models].map(([name, count]) => `${name} × ${count}`).join(' · ')
}
