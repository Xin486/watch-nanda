<template>
  <div class="dashboard-container">
    <header class="page-heading">
      <div><p class="eyebrow">基础设施监控</p><h1>{{ currentGroup || '节点总览' }}<span v-if="currentGroup" class="group-tag">分组</span></h1><p class="page-description">集中查看节点状态与资源负载，点击卡片查看详情。</p></div>
      <div class="heading-actions"><span class="clock"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>{{ currentTime }}</span><router-link to="/bigscreen" class="screen-link"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>算力大屏<span aria-hidden="true">↗</span></router-link></div>
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
          <footer class="card-footer"><span>{{ collectedAge(server.data_age_seconds) }}</span><span class="detail-link">查看详情 <span aria-hidden="true">↗</span></span></footer>
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
import { statusLabel, ageLabel } from '../utils/serverStatus'
import { useServers } from '../composables/useServers'
import { nodeMetrics, gpuSummary, gpuMemoryAverage } from '../utils/dashboardMetrics'

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
.dashboard-container{max-width:1920px;margin:0 auto;padding:30px 32px 40px;color:#172b46;box-sizing:border-box}.dashboard-container *{box-sizing:border-box}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:25px}.eyebrow{font-size:11px;font-weight:600;letter-spacing:2px;color:#64748b;margin:0 0 8px}.page-heading h1{font-size:26px;letter-spacing:-.7px;line-height:1.3;margin:0;display:flex;align-items:center;gap:12px;overflow-wrap:anywhere}.group-tag{font-size:11px;font-weight:500;letter-spacing:0;padding:4px 9px;background:#eaf1ff;color:#3860ad;border-radius:6px;flex-shrink:0}.page-description{font-size:12px;color:#75859a;margin:9px 0 0;line-height:1.7}.heading-actions{display:flex;align-items:center;gap:20px;flex-shrink:0}.clock{display:flex;align-items:center;gap:7px;color:#75859a;font-size:12px;font-variant-numeric:tabular-nums}svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}.clock svg{width:15px;height:15px}.screen-link{display:flex;align-items:center;gap:9px;padding:11px 15px;border:1px solid #dbe5f1;border-radius:9px;background:#fff;text-decoration:none;color:#375479;font-size:12px;font-weight:600;box-shadow:0 2px 3px #1e3a5f03}.screen-link>span{color:#94a3b8;margin-left:5px}.screen-link:hover{border-color:#93b4ec;background:#eff6ff}
.summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:28px}.summary-card{--status-color:#526b90;position:relative;text-align:left;font:inherit;padding:17px 19px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;cursor:pointer;transition:border-color .18s,box-shadow .18s;overflow:hidden}.summary-card.selected{border-color:#a8c2ef;box-shadow:0 0 0 1px #dbeafe}.summary-card.selected:after{content:'';position:absolute;bottom:0;left:19px;right:19px;height:2px;background:var(--status-color);border-radius:2px}.summary-card:hover{border-color:#a8c2ef}.summary-label{display:flex;align-items:center;gap:8px;font-size:12px;color:#64748b}.status-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--status-color,#94a3b8);flex-shrink:0}.summary-card strong{display:block;font-size:29px;line-height:1.3;font-weight:650;color:#1c304d;margin:10px 0 8px;font-variant-numeric:tabular-nums;letter-spacing:-1px}.summary-card strong small{font-size:11px;letter-spacing:0;color:#94a3b8;font-weight:400;margin-left:8px}.summary-note{font-size:10px;color:#8b98aa;line-height:1.5}.online{--status-color:#0d9488}.offline{--status-color:#dc5266}.stale{--status-color:#d69725}.pending{--status-color:#94a3b8}
.list-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.list-heading{display:flex;align-items:center;gap:10px;flex-shrink:0}.list-heading h2{font-size:15px;font-weight:650;margin:0}.result-count{font-size:11px;color:#64748b;background:#eaf0f6;padding:4px 8px;border-radius:5px;font-variant-numeric:tabular-nums}.list-actions{display:flex;align-items:center;gap:12px;min-width:0}.search-box{display:flex;align-items:center;gap:9px;padding:0 12px;background:#fff;border:1px solid #dfe6ef;border-radius:8px;width:255px;height:38px;color:#94a3b8}.search-box input{width:100%;min-width:0;border:0;background:transparent;outline:none;font:inherit;font-size:12px;color:#334155}.search-box input::placeholder{color:#94a3b8}.search-box:focus-within{border-color:#8eafe4;box-shadow:0 0 0 3px #dbeafe70}.search-box svg{width:16px;height:16px}.sort-box{display:flex;align-items:center;gap:8px;font-size:11px;color:#94a3b8}.sort-box select{height:38px;padding:0 26px 0 10px;border:1px solid #dfe6ef;border-radius:8px;background:#fff;color:#52647d;font:inherit;font-size:12px;max-width:150px;cursor:pointer}
.server-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:18px}.server-card{min-width:0;display:flex;flex-direction:column;border:1px solid #e0e7ef;border-radius:13px;background:white;cursor:pointer;box-shadow:0 2px 5px #1e3a5f03;transition:box-shadow .18s,border-color .18s,transform .18s}.server-card:hover{border-color:#adc4e6;box-shadow:0 8px 24px #213b6010;transform:translateY(-2px)}.server-card.offline{border-color:#f0cdd2}.server-card.stale{border-color:#eddbb4}.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:19px 20px 0}.server-identity{min-width:0;flex:1}.node-name-line{display:flex;align-items:center;gap:10px;min-width:0}.node-name-line h3{min-width:0;margin:0;font-size:15px;font-weight:650;line-height:1.5}.node-name-line h3 button{display:block;max-width:100%;text-overflow:ellipsis;white-space:nowrap;overflow:hidden;border:0;padding:0;background:none;font:inherit;color:#213650;text-align:left;cursor:pointer}.node-address{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;color:#8492a6;margin:5px 0 0;overflow-wrap:anywhere}.status-badge{display:flex;align-items:center;gap:5px;font-size:10px;font-weight:500;padding:3px 7px;border-radius:5px;white-space:nowrap;color:var(--status-color);background:#f1f5f9}.status-badge.online{background:#eaf8f3}.status-badge.offline{background:#fdf0f1}.status-badge.stale{background:#fff8e8}.status-badge .status-dot{width:5px;height:5px}.card-meta{display:flex;gap:7px;align-items:center;padding:12px 20px 0;font-size:10px;color:#8591a3}.group-name{background:#f3f5f9;color:#718096;padding:3px 7px;border-radius:4px;max-width:55%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.collection-tag{padding:3px 7px;color:#697891;border:1px solid #e9edf4;border-radius:4px}.collection-tag.agent{color:#478676;background:#f4faf8;border-color:#e5f1ec}
.metrics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:20px 20px 18px}.metric{min-width:0}.metric-label{font-size:10px;color:#718096;white-space:nowrap}.metric strong{display:block;font-size:24px;line-height:1.3;font-weight:600;letter-spacing:-.7px;margin:6px 0 9px;font-variant-numeric:tabular-nums;color:#243954;white-space:nowrap}.metric strong small{font-size:10px;font-weight:500;letter-spacing:0;color:#8c98aa;margin-left:3px}.metric-track{height:3px;overflow:hidden;background:#edf2f7;border-radius:3px;margin-bottom:7px}.metric-track i{display:block;height:100%;border-radius:3px;background:var(--metric-color)}.metric-track.unavailable{background:repeating-linear-gradient(90deg,#e7edf4 0,#e7edf4 3px,transparent 3px,transparent 6px)}.metric-note{display:block;color:#93a0b1;font-size:9px;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.node-context{margin:0 20px 16px;border:1px solid #edf1f6;background:#f9fbfd;border-radius:8px;padding:10px 12px}.gpu-summary{display:flex;align-items:center;gap:7px;color:#65768d;min-width:0}.gpu-summary svg{width:14px;height:14px;color:#93a2b5}.gpu-summary>span{font-size:10px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1;min-width:0}.gpu-summary small{font-size:9px;white-space:nowrap;color:#8798ac}.process-summary{border-top:1px solid #e9eef5;margin-top:9px;padding-top:8px}.process-label{color:#8d9aad;font-size:9px}.process-summary p{font:10px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;color:#536780;margin:4px 0 0;min-height:34px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.card-footer{margin-top:auto;padding:11px 20px;border-top:1px solid #f0f3f7;display:flex;align-items:center;justify-content:space-between;font-size:10px;color:#97a3b4;gap:10px}.detail-link{color:#6886b1;white-space:nowrap}.detail-link>span{margin-left:5px}
.unavailable-body{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:229px;padding:24px 20px;text-align:center;color:var(--status-color,#94a3b8);background:linear-gradient(#fff,#fafbfd);margin-top:12px}.unavailable-icon{display:grid;place-items:center;font-size:26px;width:44px;height:44px;border:1px solid #e8edf3;border-radius:50%;margin-bottom:10px}.unavailable-body strong{font-size:13px;font-weight:600}.unavailable-body p{font-size:11px;line-height:1.8;color:#95a0b0;max-width:240px;margin:8px 0 0}.freshness-notice{display:flex;align-items:center;gap:10px;padding:11px 15px;margin-bottom:18px;border:1px solid #f3e4bd;border-radius:8px;background:#fffcf4;color:#a58238;font-size:12px;line-height:1.7}.empty-results{text-align:center;border:1px dashed #d6e0ed;background:#fff;border-radius:12px;padding:60px 20px;color:#94a3b8}.empty-results>svg{width:38px;height:38px;color:#9cacc1;stroke-width:1.2}.empty-results h3{font-size:15px;font-weight:500;color:#52647d;margin:16px 0 9px}.empty-results p{font-size:12px;line-height:1.8}.empty-results button{border:1px solid #cddbed;background:#f5f9ff;border-radius:7px;padding:8px 14px;color:#5278ab;font:inherit;font-size:12px;cursor:pointer;margin-top:8px}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid #93c5fd;outline-offset:3px}
@media(min-width:1600px){.server-grid{grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}}
@media(max-width:1100px){.dashboard-container{padding:24px}.summary-grid{gap:10px}.summary-card{padding:15px 13px}.summary-note{font-size:9px}.clock{display:none}.list-toolbar{flex-wrap:wrap}.list-actions{flex:1;justify-content:flex-end}.search-box{width:230px}}
@media(max-width:800px){.summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.summary-card{padding:13px}.summary-card strong{font-size:25px;margin:6px 0 3px}.summary-note{display:none}.page-heading{align-items:flex-start}.page-heading h1{font-size:23px}.heading-actions{gap:8px}.screen-link{padding:9px 11px}.list-actions{flex-basis:100%;justify-content:flex-start}.search-box{flex:1;width:auto}.sort-box>span{display:none}}
@media(max-width:480px){.dashboard-container{padding:20px 16px 30px}.page-heading{gap:12px;flex-wrap:wrap;margin-bottom:20px}.page-description{font-size:11px}.heading-actions{width:100%}.screen-link{font-size:11px}.clock{display:flex;font-size:11px;order:2;margin-left:auto}.summary-grid{gap:8px;margin-bottom:22px}.summary-card{padding:12px}.summary-label{font-size:10px;gap:6px}.summary-card strong{font-size:24px}.list-actions{gap:8px}.sort-box select{font-size:11px;padding-left:8px;max-width:126px}.search-box{padding:0 9px;gap:6px}.search-box input{font-size:11px}.card-header{padding:17px 16px 0}.card-meta{padding:10px 16px 0}.metrics-grid{gap:10px;margin:18px 16px 16px}.metric strong{font-size:22px}.node-context{margin:0 16px 14px}.card-footer{padding:11px 16px}.node-name-line{gap:7px}.node-name-line h3{font-size:14px}}
@media(prefers-reduced-motion:reduce){.server-card,.summary-card{transition:none}.server-card:hover{transform:none}}
</style>
