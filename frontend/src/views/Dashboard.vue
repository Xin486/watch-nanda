<template>
  <div class="dashboard-container">
    <header class="page-heading">
      <div><p class="eyebrow">基础设施监控</p><h1>{{ currentGroup || '节点总览' }}<span v-if="currentGroup" class="group-tag">分组</span></h1><p class="page-description">集中查看节点状态与资源负载，点击卡片查看详情。</p></div>
      <div class="heading-actions"><span class="clock"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>{{ currentTime }}</span><router-link to="/bigscreen" class="screen-link"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>算力大屏<span aria-hidden="true">↗</span></router-link><ThemeToggle/></div>
    </header>
    <section class="summary-grid" aria-label="节点状态统计">
      <button v-for="item in summary" :key="item.status" class="summary-card" :class="[item.status, { selected: statusFilter === item.status }]" :aria-pressed="statusFilter === item.status" @click="statusFilter = item.status">
        <span class="summary-label"><i class="status-dot" aria-hidden="true"/>{{ item.label }}</span><strong>{{ item.count }}<small>台</small></strong><span class="summary-note">{{ item.note }}</span>
      </button>
    </section>
    <section class="node-section" aria-label="节点列表">
      <div class="list-toolbar">
        <div class="list-heading"><h2>{{ statusFilter === 'all' ? '全部节点' : statusLabel(statusFilter) + '节点' }}</h2><span class="result-count">{{ filteredServers.length }} / {{ scopedServers.length }}</span></div>
        <div class="list-actions"><label class="search-box"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg><input v-model="searchQuery" type="search" placeholder="搜索名称、IP 或分组" aria-label="搜索节点"></label><label class="sort-box"><span>排序</span><select v-model="sortBy" aria-label="节点排序"><option value="default">默认顺序</option><option value="cpu">CPU 从高到低</option><option value="gpu">显存从高到低</option><option value="name">节点名称</option></select></label></div>
      </div>
      <div v-if="staleCount" class="freshness-notice" role="status"><span aria-hidden="true">ⓘ</span>{{ staleCount }} 台节点的数据已过期，正在等待更新；数据过期不代表节点离线。</div>
      <div v-if="!filteredServers.length" class="empty-results"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5"/></svg><h3>暂无匹配的节点</h3><p>{{ searchQuery || statusFilter !== 'all' ? '试试其他关键词，或调整状态筛选。' : '节点接入后将在这里显示监控数据。' }}</p><button v-if="searchQuery || statusFilter !== 'all'" @click="clearFilters">清除筛选</button></div>
      <div class="server-grid">
        <article v-for="server in filteredServers" :key="server.id" class="server-card" :class="server.status" @click="openDetail(server)">
          <header class="card-header"><div class="server-identity"><div class="node-name-line"><h3><button :title="server.hostname" :aria-label="`${server.hostname}，查看节点详情`" @click.stop="openDetail(server)">{{ server.hostname }}</button></h3><span class="status-badge" :class="server.status"><i class="status-dot" aria-hidden="true"/>{{ statusLabel(server.status) }}</span></div><p class="node-address">{{ server.ip_address }}<span v-if="server.ssh_port && server.ssh_port !== 22">:{{ server.ssh_port }}</span></p></div><NodeAlertToggle :node="server"/></header>
          <div class="card-meta"><span class="group-name" :title="server.group_name || '未分组'">{{ server.group_name || '未分组' }}</span><span class="collection-tag" :class="{ agent: server.collection_mode === 'agent' }">{{ server.collection_mode === 'agent' ? 'Agent 上报' : 'SSH 采集' }}</span></div>
          <template v-if="server.status === 'online'">
            <div class="metrics-grid"><div v-for="metric in nodeMetrics(server)" :key="metric.label" class="metric" :style="{ '--metric-color': metric.color }"><span class="metric-label">{{ metric.label }}</span><strong>{{ metric.value }}<small v-if="metric.value !== '—'">{{ metric.unit }}</small></strong><div class="metric-track" :class="{ unavailable: metric.percent == null }"><i v-if="metric.percent != null" :style="{ width: `${metric.percent}%` }"/></div><span class="metric-note" :title="metric.note">{{ metric.note }}</span></div></div>
            <div class="node-context"><div class="gpu-summary"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/></svg><span :title="gpuSummary(server.gpu_data)">{{ gpuSummary(server.gpu_data) }}</span><small>{{ server.gpu_data?.length || 0 }} 张</small></div><div class="process-summary"><span class="process-label">CPU 占用最高进程</span><p :title="server.top_process || '暂无进程数据'">{{ server.top_process || '暂无进程数据' }}</p></div></div>
          </template>
          <div v-else class="unavailable-body"><span class="unavailable-icon" aria-hidden="true">{{ server.status === 'offline' ? '○' : '◷' }}</span><strong>{{ statusLabel(server.status) }}</strong><p>{{ server.status === 'stale' ? '指标暂不展示，等待新的采集数据' : server.status === 'pending' ? '等待首次采集完成' : server.collection_mode === 'agent' ? '尚未收到有效上报，请检查节点采集服务' : 'SSH 采集未能建立连接，请检查连接配置' }}</p></div>
          <footer class="card-footer"><span>{{ collectedAge(server.data_age_seconds) }}</span><button type="button" class="hermes-node-shortcut" :disabled="!server.hostname?.trim()" :aria-label="`复制节点名 ${server.hostname} 并填入 Hermes 对话框`" title="复制节点名并填入 Hermes" @click.stop="openHermesForNode(server.hostname)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-7l-5 3v-3H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 8v6m8-6v6m-8-3h8"/></svg><span>Hermes</span></button><span class="detail-link">查看详情 <span aria-hidden="true">↗</span></span></footer>
        </article>
      </div>
    </section>
    <NodeDetailModal v-if="selectedId !== null" :server-id="selectedId" @close="selectedId = null" @saved="refreshServers" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import NodeAlertToggle from '../components/NodeAlertToggle.vue'
import NodeDetailModal from '../components/NodeDetailModal.vue'
import ThemeToggle from '../components/ThemeToggle.vue'
import { statusLabel, ageLabel } from '../utils/serverStatus'
import { useServers } from '../composables/useServers'
import { nodeMetrics, gpuSummary, gpuMemoryAverage } from '../utils/dashboardMetrics'
import { openHermesForNode } from '../utils/hermesNodeShortcut'

const route = useRoute()
const { servers, subscribe } = useServers()
const searchQuery = ref(''), statusFilter = ref('all'), sortBy = ref('default'), selectedId = ref(null)
const currentTime = ref(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
let timer = null
const currentGroup = computed(() => typeof route.query.group === 'string' ? route.query.group : '')
const scopedServers = computed(() => currentGroup.value ? servers.value.filter(s => s.group_name === currentGroup.value) : servers.value)
const summary = computed(() => {
  const counts = scopedServers.value.reduce((acc, s) => { acc[s.status] = (acc[s.status] || 0) + 1; return acc }, {})
  return [
    { status: 'all', label: '总节点', count: scopedServers.value.length, note: currentGroup.value ? '当前分组的全部节点' : '已接入监控的全部节点' },
    { status: 'online', label: '在线', count: counts.online || 0, note: '采集数据正常更新' },
    { status: 'offline', label: '离线', count: counts.offline || 0, note: '连接或上报需要检查' },
    { status: 'stale', label: '数据过期', count: counts.stale || 0, note: '等待下一次有效更新' },
    { status: 'pending', label: '待采集', count: counts.pending || 0, note: '尚未完成首次采集' },
  ]
})
const staleCount = computed(() => summary.value.find(s => s.status === 'stale').count)
const filteredServers = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  const list = scopedServers.value.filter(s => (statusFilter.value === 'all' || s.status === statusFilter.value) && (!query || [s.hostname, s.ip_address, s.group_name].some(value => String(value || '').toLocaleLowerCase().includes(query))))
  if (sortBy.value === 'name') return list.sort((a, b) => String(a.hostname || '').localeCompare(String(b.hostname || ''), 'zh-CN', { numeric: true }))
  if (sortBy.value !== 'default') {
    const value = s => s.status !== 'online' ? -1 : sortBy.value === 'cpu' ? (s.cpu_percent ?? -1) : (gpuMemoryAverage(s.gpu_data) ?? -1)
    list.sort((a, b) => value(b) - value(a))
  }
  return list
})
function collectedAge(seconds) {
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)} 天前采集`
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} 小时前采集`
  return ageLabel(seconds)
}
function clearFilters() { searchQuery.value = ''; statusFilter.value = 'all' }
function openDetail(server) { selectedId.value = server.id }
const refreshServers = () => window.dispatchEvent(new CustomEvent('servers-refresh'))
onMounted(() => {
  subscribe()
  timer = setInterval(() => { currentTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false }) }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.server-card .card-footer{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)}.card-footer>.detail-link{justify-self:end}.hermes-node-shortcut{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-height:28px;padding:4px 8px;border:1px solid var(--accent-border);border-radius:7px;color:var(--accent);background:var(--accent-soft);font:inherit;cursor:pointer}.hermes-node-shortcut svg{width:14px;height:14px}.hermes-node-shortcut:hover{border-color:var(--accent);background:var(--surface-muted)}.hermes-node-shortcut:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.dashboard-container{width:100%;max-width:100%;min-width:0;margin:0 auto;padding:30px 32px 40px;color:var(--text);box-sizing:border-box}.dashboard-container *{box-sizing:border-box}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:25px}.eyebrow{font-size:11px;font-weight:600;letter-spacing:2px;color:var(--text-muted);margin:0 0 8px}.page-heading h1{font-size:26px;letter-spacing:-.7px;line-height:1.3;margin:0;display:flex;align-items:center;gap:12px;overflow-wrap:anywhere}.group-tag{font-size:11px;font-weight:500;letter-spacing:0;padding:4px 9px;background:var(--accent-soft);color:var(--text-secondary);border-radius:6px;flex-shrink:0}.page-description{font-size:12px;color:var(--text-muted);margin:9px 0 0;line-height:1.7}.heading-actions{display:flex;align-items:center;gap:20px;flex-shrink:0}.clock{display:flex;align-items:center;gap:7px;color:var(--text-muted);font-size:12px;font-variant-numeric:tabular-nums}svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}.clock svg{width:15px;height:15px}.screen-link{display:flex;align-items:center;gap:9px;padding:11px 15px;border:1px solid var(--border);border-radius:9px;background:var(--surface);text-decoration:none;color:var(--text-secondary);font-size:12px;font-weight:600;box-shadow:var(--shadow)}.screen-link>span{color:var(--text-muted);margin-left:5px}.screen-link:hover{border-color:var(--accent-border);background:var(--accent-soft)}
.summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:28px}.summary-card{--status-color:var(--accent);position:relative;text-align:left;font:inherit;padding:17px 19px;background:var(--surface);border:1px solid var(--border);border-radius:12px;cursor:pointer;transition:border-color .18s,box-shadow .18s;overflow:hidden}.summary-card.selected{border-color:var(--accent-border);box-shadow:0 0 0 1px var(--accent-border)}.summary-card.selected:after{content:'';position:absolute;bottom:0;left:19px;right:19px;height:2px;background:var(--status-color);border-radius:2px}.summary-card:hover{border-color:var(--accent-border)}.summary-label{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-muted)}.status-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--status-color,var(--text-muted));flex-shrink:0}.summary-card strong{display:block;font-size:29px;line-height:1.3;font-weight:650;color:var(--text);margin:10px 0 8px;font-variant-numeric:tabular-nums;letter-spacing:-1px}.summary-card strong small{font-size:11px;letter-spacing:0;color:var(--text-muted);font-weight:400;margin-left:8px}.summary-note{font-size:10px;color:var(--text-muted);line-height:1.5}.online{--status-color:var(--success)}.offline{--status-color:var(--danger)}.stale{--status-color:var(--warning)}.pending{--status-color:var(--text-muted)}
.list-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.list-heading{display:flex;align-items:center;gap:10px;flex-shrink:0}.list-heading h2{font-size:15px;font-weight:650;margin:0}.result-count{font-size:11px;color:var(--text-muted);background:var(--surface-muted);padding:4px 8px;border-radius:5px;font-variant-numeric:tabular-nums}.list-actions{display:flex;align-items:center;gap:12px;min-width:0}.search-box{display:flex;align-items:center;gap:9px;padding:0 12px;background:var(--surface);border:1px solid var(--border);border-radius:8px;width:255px;height:38px;color:var(--text-muted)}.search-box input{width:100%;min-width:0;border:0;background:transparent;outline:none;font:inherit;font-size:12px;color:var(--text)}.search-box input::placeholder{color:var(--text-muted)}.search-box:focus-within{border-color:var(--accent-border);box-shadow:0 0 0 3px var(--accent-soft)}.search-box svg{width:16px;height:16px}.sort-box{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-muted)}.sort-box select{height:38px;padding:0 26px 0 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text-secondary);font:inherit;font-size:12px;max-width:150px;cursor:pointer}
.server-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:18px}.server-card{min-width:0;container:node-card / inline-size;display:flex;flex-direction:column;border:1px solid var(--border);border-radius:13px;background:var(--surface);cursor:pointer;box-shadow:var(--shadow);transition:box-shadow .18s,border-color .18s,transform .18s}.server-card:hover{border-color:var(--accent-border);box-shadow:var(--shadow-hover);transform:translateY(-2px)}.server-card.offline{border-color:var(--danger-border)}.server-card.stale{border-color:var(--warning-border)}.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:19px 20px 0}.server-identity{min-width:0;flex:1}.node-name-line{display:flex;align-items:center;gap:10px;min-width:0}.node-name-line h3{min-width:0;margin:0;font-size:15px;font-weight:650;line-height:1.5}.node-name-line h3 button{display:block;max-width:100%;text-overflow:ellipsis;white-space:nowrap;overflow:hidden;border:0;padding:0;background:none;font:inherit;color:var(--text);text-align:left;cursor:pointer}.node-address{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--text-muted);margin:5px 0 0;overflow-wrap:anywhere}.status-badge{display:flex;align-items:center;gap:5px;font-size:10px;font-weight:500;padding:3px 7px;border-radius:5px;white-space:nowrap;color:var(--status-color);background:var(--surface-muted)}.status-badge.online{background:var(--success-bg)}.status-badge.offline{background:var(--danger-bg)}.status-badge.stale{background:var(--warning-bg)}.status-badge .status-dot{width:5px;height:5px}.card-meta{display:flex;gap:7px;align-items:center;padding:12px 20px 0;font-size:10px;color:var(--text-muted)}.group-name{background:var(--surface-soft);color:var(--text-muted);padding:3px 7px;border-radius:4px;max-width:55%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.collection-tag{padding:3px 7px;color:var(--text-muted);border:1px solid var(--border-soft);border-radius:4px}.collection-tag.agent{color:var(--success);background:var(--success-bg);border-color:var(--success-border)}
.metrics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:20px 20px 18px}.metric{min-width:0}.metric-label{font-size:10px;color:var(--text-muted);white-space:nowrap}.metric strong{display:block;font-size:24px;line-height:1.3;font-weight:600;letter-spacing:-.7px;margin:6px 0 9px;font-variant-numeric:tabular-nums;color:var(--text);white-space:nowrap}.metric strong small{font-size:10px;font-weight:500;letter-spacing:0;color:var(--text-muted);margin-left:3px}.metric-track{height:3px;overflow:hidden;background:var(--border-soft);border-radius:3px;margin-bottom:7px}.metric-track i{display:block;height:100%;border-radius:3px;background:var(--metric-color)}.metric-track.unavailable{background:repeating-linear-gradient(90deg,var(--track) 0,var(--track) 3px,transparent 3px,transparent 6px)}.metric-note{display:block;color:var(--text-muted);font-size:9px;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.node-context{margin:0 20px 16px;border:1px solid var(--border-soft);background:var(--surface-soft);border-radius:8px;padding:10px 12px}.gpu-summary{display:flex;align-items:center;gap:7px;color:var(--text-secondary);min-width:0}.gpu-summary svg{width:14px;height:14px;color:var(--text-muted)}.gpu-summary>span{font-size:10px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1;min-width:0}.gpu-summary small{font-size:9px;white-space:nowrap;color:var(--text-muted)}.process-summary{border-top:1px solid var(--border-soft);margin-top:9px;padding-top:8px}.process-label{color:var(--text-muted);font-size:9px}.process-summary p{font:10px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--text-secondary);margin:4px 0 0;min-height:34px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.card-footer{margin-top:auto;padding:11px 20px;border-top:1px solid var(--border-soft);display:flex;align-items:center;justify-content:space-between;font-size:10px;color:var(--text-muted);gap:10px}.detail-link{color:var(--text-secondary);white-space:nowrap}.detail-link>span{margin-left:5px}
.unavailable-body{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:229px;padding:24px 20px;text-align:center;color:var(--status-color,var(--text-muted));background:linear-gradient(var(--surface),var(--surface-soft));margin-top:12px}.unavailable-icon{display:grid;place-items:center;font-size:26px;width:44px;height:44px;border:1px solid var(--border-soft);border-radius:50%;margin-bottom:10px}.unavailable-body strong{font-size:13px;font-weight:600}.unavailable-body p{font-size:11px;line-height:1.8;color:var(--text-muted);max-width:240px;margin:8px 0 0}.freshness-notice{display:flex;align-items:center;gap:10px;padding:11px 15px;margin-bottom:18px;border:1px solid var(--warning-border);border-radius:8px;background:var(--warning-bg);color:var(--warning);font-size:12px;line-height:1.7}.empty-results{text-align:center;border:1px dashed var(--border);background:var(--surface);border-radius:12px;padding:60px 20px;color:var(--text-muted)}.empty-results>svg{width:38px;height:38px;color:var(--text-muted);stroke-width:1.2}.empty-results h3{font-size:15px;font-weight:500;color:var(--text-secondary);margin:16px 0 9px}.empty-results p{font-size:12px;line-height:1.8}.empty-results button{border:1px solid var(--border);background:var(--accent-soft);border-radius:7px;padding:8px 14px;color:var(--text-secondary);font:inherit;font-size:12px;cursor:pointer;margin-top:8px}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
.page-description{font-size:13px}.summary-label{font-size:13px}.summary-note{font-size:11px;color:var(--text-muted)}.summary-card{padding-top:14px;padding-bottom:14px}.metric-label{font-size:11px}.metric-note{font-size:10px;color:var(--text-muted)}.gpu-summary>span{font-size:11px}.gpu-summary small{font-size:10px}.process-label{font-size:10px;color:var(--text-muted)}.process-summary p{font-size:11px;min-height:37px}.card-footer{font-size:11px;color:var(--text-muted)}
.heading-actions{gap:12px}.screen-link{min-height:40px}.detail-link{color:var(--accent)}
@container main (min-width:1360px){.server-grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr))}}
/* Match the available content column, including display scaling and narrower windows. */
@container main (min-width:1960px){
  .dashboard-container{padding:36px 40px 48px}
  .server-grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,380px),1fr));gap:22px}
  .summary-grid{gap:18px;margin-bottom:32px}
  .summary-card{padding:18px 22px}.summary-card strong{font-size:34px}
  .page-heading h1{font-size:30px}.list-heading h2{font-size:17px}
  .node-name-line h3{font-size:17px}.metric strong{font-size:28px}
  .metric-label,.card-footer{font-size:12px}.metric-note{font-size:11px}
  .node-address,.gpu-summary>span,.process-summary p{font-size:12px}
  .status-badge,.card-meta,.gpu-summary small,.process-label{font-size:11px}
}
@container main (min-width:2760px){
  .dashboard-container{padding:44px 56px 60px}
  .page-heading{margin-bottom:32px}.page-heading h1{font-size:34px}
  .page-description,.clock{font-size:14px}.heading-actions{gap:18px}
  .summary-grid{gap:22px;margin-bottom:38px}.summary-label{font-size:15px}
  .summary-note{font-size:12px}.summary-card{padding:22px 26px}.summary-card strong{font-size:38px}
  .list-toolbar{margin-bottom:24px}.list-heading h2{font-size:19px}
  .result-count,.sort-box,.sort-box select,.screen-link{font-size:13px}
  .search-box{width:320px;height:44px}.search-box input{font-size:14px}.sort-box select{height:44px}
  .server-grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,440px),1fr));gap:24px}
  .card-header{padding:24px 24px 0}.node-name-line h3{font-size:18px}
  .card-meta{padding:14px 24px 0}.metrics-grid{gap:16px;margin:24px 24px 22px}
  .metric-label{font-size:12px}.metric strong{font-size:30px}.metric strong small{font-size:12px}
  .node-context{margin:0 24px 20px;padding:12px 14px}.process-summary p{min-height:41px}
  .card-footer{padding:14px 24px}.unavailable-body{min-height:277px}
  .unavailable-body strong{font-size:15px}.unavailable-body p{font-size:13px;max-width:300px}
}
@container main (max-width:1100px){.dashboard-container{padding:24px}.summary-grid{gap:10px}.summary-card{padding:15px 13px}.summary-note{font-size:9px}.clock{display:none}.list-toolbar{flex-wrap:wrap}.list-actions{flex:1;justify-content:flex-end}.search-box{width:230px}}
@container main (max-width:800px){.summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.summary-card{padding:13px}.summary-card strong{font-size:25px;margin:6px 0 3px}.summary-note{display:none}.page-heading{align-items:flex-start}.page-heading h1{font-size:23px}.heading-actions{gap:8px}.screen-link{padding:9px 11px}.list-actions{flex-basis:100%;justify-content:flex-start}.search-box{flex:1;width:auto}.sort-box>span{display:none}}
@container main (max-width:480px){.dashboard-container{padding:20px 16px 30px}.page-heading{gap:12px;flex-wrap:wrap;margin-bottom:20px}.page-description{font-size:11px}.heading-actions{width:100%}.screen-link{font-size:11px}.clock{display:flex;font-size:11px;order:2;margin-left:auto}.summary-grid{gap:8px;margin-bottom:22px}.summary-card{padding:12px}.summary-label{font-size:10px;gap:6px}.summary-card strong{font-size:24px}.list-actions{gap:8px}.sort-box select{font-size:11px;padding-left:8px;max-width:126px}.search-box{padding:0 9px;gap:6px}.search-box input{font-size:11px}.card-header{padding:17px 16px 0}.card-meta{padding:10px 16px 0}.metrics-grid{gap:10px;margin:18px 16px 16px}.metric strong{font-size:22px}.node-context{margin:0 16px 14px}.card-footer{padding:11px 16px}.node-name-line{gap:7px}.node-name-line h3{font-size:14px}}
@container node-card (max-width:360px){.metrics-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(prefers-reduced-motion:reduce){.server-card,.summary-card{transition:none}.server-card:hover{transform:none}}
</style>
