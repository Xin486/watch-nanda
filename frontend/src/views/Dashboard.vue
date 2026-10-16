<template>
  <div class="dashboard-container standard-theme">
    <header class="top-bar">
      <div class="stats">
        <div class="stat-item"><span class="label">总节点</span><span class="value total-val">{{ servers.length }}</span></div>
        <div class="stat-item"><span class="label">在线</span><span class="value text-green">{{ servers.filter(s => s.status === 'online').length }}</span></div>
        <div class="stat-item"><span class="label">离线</span><span class="value text-red">{{ servers.filter(s => s.status === 'offline').length }}</span></div>
        <div class="stat-item"><span class="label">数据过期</span><span class="value text-amber">{{ servers.filter(s => s.status === 'stale').length }}</span></div>
        <div class="stat-item"><span class="label">待采集</span><span class="value text-muted">{{ servers.filter(s => s.status === 'pending').length }}</span></div>
      </div>
      <div class="center-area"><span class="time-display">🕒 {{ currentTime }}</span></div>
      <div class="filters">
        <input type="text" placeholder="全局检索节点..." class="search-input" v-model="searchQuery">
        <button
          class="btn-filter-offline"
          :class="{ active: showOfflineOnly }"
          @click="showOfflineOnly = !showOfflineOnly"
          :title="showOfflineOnly ? '当前仅显示离线节点，点击恢复全部' : '一键筛选离线节点'"
        >
          <span class="btn-icon">🔴</span>
          <span class="btn-text">{{ showOfflineOnly ? '离线筛选中' : '筛选离线' }}</span>
          <span class="offline-count" v-if="!showOfflineOnly && offlineCount > 0">{{ offlineCount }}</span>
        </button>
        <router-link to="/bigscreen" class="btn-toggle-mode">
          <span class="btn-icon">🖥️</span><span class="btn-text">进入算力大屏</span>
        </router-link>
      </div>
    </header>

    <div class="freshness-notice" v-if="servers.some(s => s.status === 'stale')">部分节点采集数据已过期，等待下一次更新；过期不代表节点已离线。</div>
    <div class="empty-results" v-if="!filteredServers.length">{{ showOfflineOnly ? '当前筛选范围内没有离线节点' : '没有匹配的节点' }}</div>
    <div class="server-grid">
      <div class="server-card clickable-card" v-for="server in filteredServers" :key="server.id" :class="server.status" @click="openEditModal(server)" tabindex="0" :aria-label="`${server.hostname}，查看节点详情`" @keydown.enter.self.prevent="openEditModal(server)" @keydown.space.self.prevent="openEditModal(server)">
        <div class="card-header">
          <div class="server-info">
            <strong class="host-title">{{ server.hostname }}</strong>
            <span class="ip-addr">{{ server.ip_address }}</span>
            <span class="time">{{ ageLabel(server.data_age_seconds) }}</span>
          </div>
          <div class="card-controls"><NodeAlertToggle :node="server"/><div class="status-badge" :class="server.status">{{ statusLabel(server.status) }}</div></div>
        </div>

        <div class="card-body" v-if="server.status === 'online'">
          <div class="rings-container">
            <RingChart :percent="server.cpu_percent" label="CPU" :subText="`${server.cpu_cores}核`" />
            <RingChart :percent="server.ram_percent" label="内存" :subText="`${Math.round(server.ram_used_mb/1024)}/${Math.round(server.ram_total_mb/1024)}G`" />
            <RingChart :percent="getGpuAvg(server.gpu_data)" label="GPU显存" :subText="`${server.gpu_data ? server.gpu_data.length : 0}张`" />
            <!-- GPU 功率圈：中间显示总功率；无上限数据时进度按 400W 参考值估算 -->
            <RingChart :percent="getGpuPowerPercent(server.gpu_data)" label="GPU功率" :centerText="Math.round(getGpuPower(server.gpu_data))" centerUnit="W" />
          </div>
          <div class="detail-info">
            <div class="info-row top-process" v-if="server.top_process">
              <span class="icon">🔥</span><span class="text">{{ server.top_process }}</span>
            </div>
            <div class="gpu-list" v-if="server.gpu_data && server.gpu_data.length > 0">
              <div class="info-row" v-for="(gpu, index) in server.gpu_data.slice(0, 2)" :key="index">
                <svg class="icon-svg" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect>
                  <line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line>
                  <line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="23" y2="20"></line>
                  <line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line>
                  <line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line>
                </svg>
                <span class="text">GPU {{ index }} 型号: {{ gpu.model }}</span>
              </div>
              <div class="more-gpu" v-if="server.gpu_data.length > 2">还有 {{ server.gpu_data.length - 2 }} 张显卡...点击查看详情</div>
            </div>
          </div>
        </div>
        <div class="card-body offline-body" v-else><span>{{ statusLabel(server.status) }}</span><small>{{ server.status === 'stale' ? '暂不展示过期指标，正在等待采集更新' : server.status === 'pending' ? '后台将自动开始采集' : (server.collection_mode === 'agent' ? '尚未收到有效上报' : 'SSH 采集未能建立连接') }}</small></div>
      </div>
    </div>

    <NodeDetailModal v-if="showEditModal" :server-id="editForm.id" @close="showEditModal = false" @saved="refreshServers" />
  </div>
</template>

<script setup>
// 首页：服务器节点卡片网格（总览 + 详情/编辑弹窗）
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import RingChart from '../components/RingChart.vue'
import NodeAlertToggle from '../components/NodeAlertToggle.vue'
import NodeDetailModal from '../components/NodeDetailModal.vue'
import { statusLabel, ageLabel } from '../utils/serverStatus'
import { useServers } from '../composables/useServers'

const route = useRoute()
const { servers, subscribe } = useServers()
const searchQuery = ref('')
const showOfflineOnly = ref(false)
const currentTime = ref(new Date().toLocaleTimeString())
let timer = null

const showEditModal = ref(false)
const editForm = ref({})

// 服务器数据通过 SSE 实时推送，无需手动拉取

const offlineCount = computed(() => servers.value.filter(s => s.status === 'offline').length)

const filteredServers = computed(() => {
  let list = servers.value
  if (route.query.group) list = list.filter(s => s.group_name === route.query.group)
  if (showOfflineOnly.value) list = list.filter(s => s.status === 'offline')
  if (searchQuery.value) {
    list = list.filter(s => (s.hostname || '').includes(searchQuery.value) || (s.ip_address || '').includes(searchQuery.value))
  }
  return list
})

const getGpuAvg = (gpuData) => {
  if (!gpuData || gpuData.length === 0) return 0
  const total = gpuData.reduce((acc, curr) => acc + (curr.memory_percent || 0), 0)
  return Math.round(total / gpuData.length)
}

// ---- GPU 功耗计算（power_draw / power_limit 由后端 nvidia-smi 采集）----
const getGpuPower = (gpuData) => (gpuData || []).reduce((sum, g) => sum + (g.power_draw || 0), 0)
const getGpuPowerLimit = (gpuData) => (gpuData || []).reduce((sum, g) => sum + (g.power_limit || 0), 0)
const getGpuPowerPercent = (gpuData) => {
  const limit = getGpuPowerLimit(gpuData)
  if (limit > 0) return Math.round((getGpuPower(gpuData) / limit) * 100)
  // 部分新驱动/卡型拿不到功耗上限（如 4090），按 400W 参考值估算进度，仅作视觉参考
  return Math.min(Math.round((getGpuPower(gpuData) / 400) * 100), 100)
}

const formatTime = (isoString) => isoString ? new Date(isoString).toLocaleTimeString() : '--:--:--'

// 打开详情/编辑弹窗（深拷贝一份，避免直接改动列表数据）
const openEditModal = (server) => {
  editForm.value = { 
    id: server.id, 
    hostname: server.hostname, 
    ip_address: server.ip_address, 
    group_name: server.group_name,
    ssh_user: server.ssh_user || 'root', 
    ssh_port: server.ssh_port || 22, 
    collection_mode: server.collection_mode || 'ssh',
    ssh_password: '',
  }
  showEditModal.value = true
}

const refreshServers = () => window.dispatchEvent(new CustomEvent('servers-refresh'))

onMounted(() => {
  subscribe()   // 建立 SSE 连接，实时接收服务器数据
  timer = setInterval(() => { currentTime.value = new Date().toLocaleTimeString() }, 1000)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.card-controls{display:flex;align-items:center;gap:9px;flex-shrink:0}.server-card:focus-visible{outline:3px solid #93c5fd;outline-offset:3px}.card-header{gap:12px}.server-info{flex:1}.server-card{border-radius:13px!important;overflow:visible}.host-title{font-weight:650}.status-badge{border-radius:20px!important;padding:4px 8px!important;white-space:nowrap}

/* ==================== 基础布局 & 首页样式 ==================== */
.dashboard-container { padding: 20px; background: #f8fafc; min-height: 100vh; }
.top-bar { display: flex; justify-content: space-between; align-items: center; padding: 15px 25px; border-radius: 8px; margin-bottom: 20px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.stats { display: flex; gap: 30px; }
.stat-item { display: flex; flex-direction: column; align-items: center; }
.stat-item .label { font-size: 12px; margin-bottom: 4px; color: #64748b; }
.stat-item .value { font-size: 20px; font-weight: bold; }
.text-green { color: #10b981 !important; }
.text-red { color: #ef4444 !important; }
.time-display { color: #64748b; font-size: 14px; }
.filters { display: flex; gap: 15px; align-items: center; }
.search-input { padding: 8px 12px; border-radius: 4px; outline: none; width: 180px; font-size: 13px; border: 1px solid #e2e8f0; }

.btn-toggle-mode { display: flex; align-items: center; gap: 6px; padding: 8px 18px; border-radius: 20px; font-weight: 600; cursor: pointer; font-size: 13px; text-decoration: none; background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); color: #fff; box-shadow: 0 2px 6px rgba(37,99,235, 0.3); }

.btn-filter-offline { position: relative; display: flex; align-items: center; gap: 6px; padding: 8px 18px; border-radius: 20px; font-weight: 600; cursor: pointer; font-size: 13px; border: 1.5px solid #e2e8f0; background: #fff; color: #475569; transition: all 0.2s; }
.btn-filter-offline:hover { border-color: #fca5a5; background: #fef2f2; color: #ef4444; }
.btn-filter-offline.active { background: #fef2f2; border-color: #ef4444; color: #ef4444; box-shadow: 0 2px 6px rgba(239,68,68,0.2); }
.offline-count { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: #ef4444; color: #fff; font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; line-height: 1; }

.server-grid { display: grid; gap: 18px; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }
.server-card { border-radius: 8px; border: 1px solid #e2e8f0; background: #fff; transition: transform 0.2s, box-shadow 0.2s; }
.clickable-card { cursor: pointer; }
.clickable-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
.server-card.offline { border-color: #fca5a5; background: #fef2f2; }

.card-header { padding: 12px 15px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; }
.server-info { display: flex; gap: 10px; align-items: center; font-size: 13px; }
.host-title { font-size: 15px; color: #1e293b; }
.ip-addr { color: #64748b; font-family: monospace; }
.time { color: #94a3b8; font-size: 11px; }

.status-badge { font-size: 11px; font-weight: 800; padding: 2px 6px; border-radius: 3px; }
.status-badge.online { background: #e0f2fe; color: #0284c7; }
.status-badge.offline { background: #fee2e2; color: #ef4444; }

.card-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.rings-container { display: flex; justify-content: space-around; width: 100%; }
.detail-info { border-radius: 6px; padding: 10px; font-size: 12px; background: #f8fafc; color: #475569; }
.info-row { display: flex; align-items: center; margin-bottom: 5px; }
.top-process { color: #ea580c; border-bottom: 1px solid #e2e8f0; font-weight: bold; padding-bottom: 4px; }
.icon-svg { margin-right: 6px; vertical-align: text-bottom; color: #64748b; }
.icon-svg-blue { margin-right: 6px; vertical-align: text-bottom; color: #3b82f6; }
.more-gpu { text-align: center; font-size: 11px; color: #2563eb; font-weight: bold; margin-top: 4px; }
.offline-body { font-weight: bold; align-items: center; justify-content: center; height: 90px; color: #ef4444; font-family: monospace; }

.text-amber { color: #d97706; }
.text-muted { color: #64748b; }
.server-card.stale { border-color: #fcd34d; background: #fffdf5; }
.server-card.pending { border-color: #cbd5e1; background: #f8fafc; }
.status-badge.stale { color: #92400e; background: #fef3c7; }
.status-badge.pending { color: #475569; background: #e2e8f0; }
.stale .offline-body { color: #b45309; }
.pending .offline-body { color: #64748b; }
.offline-body small { font-weight: 400; font-size: 12px; }
.freshness-notice { padding: 12px 16px; margin-bottom: 18px; border: 1px solid #fde68a; border-radius: 8px; background: #fffbeb; color: #92400e; font-size: 13px; }
.empty-results { padding: 64px 20px; text-align: center; color: #64748b; }
.metrics-updated { font-size: 12px; color: #64748b; margin-top: -5px; }
.top-bar { flex-wrap: wrap; gap: 18px; }
.stats { gap: 20px; }
.server-info { flex-wrap: wrap; min-width: 0; }
.host-title { overflow-wrap: anywhere; }
.modal-content { max-height: 90vh; overflow-y: auto; box-sizing: border-box; }
@media (max-width: 1100px) { .center-area { display: none; } .filters { flex-wrap: wrap; } }
@media (max-width: 700px) { .dual-layout { grid-template-columns: 1fr; } .server-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
