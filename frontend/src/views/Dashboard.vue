<template>
  <div class="dashboard-container standard-theme">
    <header class="top-bar">
      <div class="stats">
        <div class="stat-item"><span class="label">总节点</span><span class="value total-val">{{ servers.length }}</span></div>
        <div class="stat-item"><span class="label">在线</span><span class="value text-green">{{ servers.filter(s => s.status === 'online').length }}</span></div>
        <div class="stat-item"><span class="label">离线</span><span class="value text-red">{{ servers.filter(s => s.status === 'offline').length }}</span></div>
      </div>
      <div class="center-area"><span class="time-display">🕒 {{ currentTime }}</span></div>
      <div class="filters">
        <input type="text" placeholder="全局检索节点..." class="search-input" v-model="searchQuery">
        <router-link to="/bigscreen" class="btn-toggle-mode">
          <span class="btn-icon">🖥️</span><span class="btn-text">进入算力大屏</span>
        </router-link>
      </div>
    </header>

    <div class="server-grid">
      <div class="server-card clickable-card" v-for="server in filteredServers" :key="server.id" :class="server.status" @click="openEditModal(server)">
        <div class="card-header">
          <div class="server-info">
            <strong class="host-title">{{ server.hostname }}</strong>
            <span class="ip-addr">{{ server.ip_address }}</span>
            <span class="time">更新: {{ formatTime(server.timestamp) }}</span>
          </div>
          <div class="status-badge" :class="server.status">{{ server.status === 'online' ? 'ON' : 'OFF' }}</div>
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
        <div class="card-body offline-body" v-else>NODE OFFLINE</div>
      </div>
    </div>

    <div class="modal-overlay" v-if="showEditModal" @click.self="showEditModal = false">
      <div class="modal-content detailed-panel">
        <div class="modal-header">
          <h3>🖥️ 节点详情与配置 - {{ editForm.hostname }}</h3>
          <span class="modal-close" @click="showEditModal = false">✖</span>
        </div>
        
        <div class="dual-layout">
          <div class="metrics-pane">
            <h4 class="pane-title">📊 实时性能指标</h4>
            <div class="metric-box" v-if="editForm.status === 'online'">
              <div class="metric-item">
                <div class="metric-label"><span>CPU 使用率</span><span>{{ editForm.cpu_percent }}% ({{ editForm.cpu_cores }} 核)</span></div>
                <div class="progress-bar"><div class="progress-fill bg-green" :style="{ width: editForm.cpu_percent + '%' }"></div></div>
              </div>
              <div class="metric-item">
                <div class="metric-label"><span>内存使用率</span><span>{{ Math.round(editForm.ram_used_mb/1024) }}G / {{ Math.round(editForm.ram_total_mb/1024) }}G ({{ editForm.ram_percent }}%)</span></div>
                <div class="progress-bar"><div class="progress-fill bg-amber" :style="{ width: editForm.ram_percent + '%' }"></div></div>
              </div>
              <div class="metric-item top-proc-box">
                <div class="metric-label"><span class="text-orange">🔥 当前最高占用进程</span></div>
                <div class="proc-name">{{ editForm.top_process }}</div>
              </div>
              <div class="gpu-detailed-section" v-if="editForm.gpu_data && editForm.gpu_data.length > 0">
                <div class="metric-label"><span>🎮 NVIDIA 显卡详情 ({{ editForm.gpu_data.length }} 张)</span></div>
                <div class="gpu-detail-card" v-for="(gpu, index) in editForm.gpu_data" :key="index">
                  <div class="gpu-meta">
                    <span class="gpu-idx">
                      <svg class="icon-svg-blue" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>
                      GPU {{ index }}
                    </span>
                    <span class="gpu-model">{{ gpu.model }}</span>
                    <span class="gpu-temp" :class="{ 'hot': parseInt(gpu.temp) > 80 }">🌡️ {{ gpu.temp }}°C</span>
                  </div>
                  <div class="gpu-mem-text">显存: {{ gpu.memory_used }} / {{ gpu.memory_total }} ({{ gpu.memory_percent }}%)</div>
                  <div class="progress-bar mini"><div class="progress-fill bg-red" :style="{ width: gpu.memory_percent + '%' }"></div></div>
                </div>
              </div>
              <div class="no-gpu-hint" v-else>❌ 该服务器未检测到 NVIDIA 显卡</div>
            </div>
            <div class="modal-offline-state" v-else>⚠️ 节点当前处于离线状态，无法抓取实时监控数据。</div>
          </div>

          <div class="form-pane">
            <h4 class="pane-title">⚙️ 节点配置修改</h4>
            <div class="form-grid">
              <div class="form-item"><label>主机名</label><input type="text" v-model="editForm.hostname"></div>
              <div class="form-item"><label>IP 地址</label><input type="text" v-model="editForm.ip_address"></div>
              <div class="form-item">
                <label>分组名称</label>
                <input type="text" v-model="editForm.group_name" list="group-suggestions">
                <datalist id="group-suggestions">
                  <option v-for="g in availableGroups" :key="g" :value="g"></option>
                </datalist>
              </div>
              <div class="form-item"><label>SSH 用户名</label><input type="text" v-model="editForm.ssh_user"></div>
              <div class="form-item"><label>SSH 端口</label><input type="number" v-model="editForm.ssh_port"></div>
            </div>
            <div class="modal-actions">
              <button class="btn-cancel" @click="showEditModal = false">取消</button>
              <button class="btn-save" @click="saveServerInfo">保存配置修改</button>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
// 首页：服务器节点卡片网格（总览 + 详情/编辑弹窗）
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import RingChart from '../components/RingChart.vue'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const route = useRoute()
const { servers, subscribe } = useServers()
const searchQuery = ref('')
const currentTime = ref(new Date().toLocaleTimeString())
let timer = null

const showEditModal = ref(false)
const editForm = ref({})
const availableGroups = ref(JSON.parse(localStorage.getItem('customGroups') || '[]'))

// 服务器数据通过 SSE 实时推送，无需手动拉取

const filteredServers = computed(() => {
  let list = servers.value
  if (route.query.group) list = list.filter(s => s.group_name === route.query.group)
  if (searchQuery.value) {
    list = list.filter(s => s.hostname.includes(searchQuery.value) || s.ip_address.includes(searchQuery.value))
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
    status: server.status,
    cpu_percent: server.cpu_percent || 0, 
    cpu_cores: server.cpu_cores || 1, 
    ram_percent: server.ram_percent || 0,
    ram_used_mb: server.ram_used_mb || 0, 
    ram_total_mb: server.ram_total_mb || 0, 
    gpu_data: server.gpu_data || [],
    top_process: server.top_process || '暂无'
  }
  showEditModal.value = true
}

// 提交节点配置修改到后端
const saveServerInfo = async () => {
  try {
    await api.updateServer(editForm.value.id, editForm.value)
    showEditModal.value = false
    window.dispatchEvent(new CustomEvent('servers-refresh')) 
  } catch (error) { 
    alert('配置数据保存同步失败，请检查后端 FastAPI 状态。') 
  }
}

onMounted(() => {
  subscribe()   // 建立 SSE 连接，实时接收服务器数据
  timer = setInterval(() => { currentTime.value = new Date().toLocaleTimeString() }, 1000)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
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

/* ==================== 弹窗双栏高级样式 (100% 完整版) ==================== */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vw; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; height: 100vh;}
.detailed-panel { background: #fff; padding: 25px; border-radius: 12px; width: 850px; max-width: 95vw; box-shadow: 0 10px 30px rgba(0,0,0,0.2); color: #1e293b; }
.modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; }
.modal-header h3 { margin: 0; color: #0f172a; font-size: 18px; }
.modal-close { cursor: pointer; color: #94a3b8; font-size: 18px; }
.modal-close:hover { color: #ef4444; }

.dual-layout { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 30px; margin-top: 20px; }
.pane-title { margin-top: 0; margin-bottom: 15px; color: #334155; border-left: 4px solid #3b82f6; padding-left: 8px; font-size: 14px; }

.metrics-pane { background: #f8fafc; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; max-height: 450px; overflow-y: auto; }
.metric-item { margin-bottom: 16px; }
.metric-label { display: flex; justify-content: space-between; font-size: 12px; color: #475569; font-weight: bold; margin-bottom: 6px; }
.text-orange { color: #ea580c; }
.progress-bar { background: #e2e8f0; height: 8px; border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; transition: width 0.4s ease; }
.bg-green { background: #10b981; }
.bg-amber { background: #f59e0b; }
.bg-red { background: #ef4444; }

.top-proc-box { background: #fff; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; }
.proc-name { font-family: monospace; font-size: 12px; color: #1e293b; margin-top: 4px; word-break: break-all; }

.gpu-detailed-section { margin-top: 20px; border-top: 1px solid #e2e8f0; padding-top: 15px; }
.gpu-detail-card { background: #fff; padding: 12px; border-radius: 6px; border: 1px solid #e2e8f0; margin-bottom: 10px; }
.gpu-meta { display: flex; justify-content: space-between; font-size: 12px; font-weight: bold; margin-bottom: 6px; }
.gpu-idx { color: #3b82f6; display: flex; align-items: center; gap: 4px; }
.gpu-model { color: #1e293b; flex: 1; margin-left: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gpu-temp { color: #10b981; }
.gpu-temp.hot { color: #ef4444; }
.gpu-mem-text { font-size: 11px; color: #64748b; margin-bottom: 4px; }
.progress-bar.mini { height: 5px; }
.no-gpu-hint { font-size: 12px; color: #94a3b8; text-align: center; margin-top: 15px; }
.modal-offline-state { height: 150px; display: flex; align-items: center; justify-content: center; color: #ef4444; font-weight: bold; font-size: 13px; }

.form-pane { display: flex; flex-direction: column; justify-content: space-between; }
.form-grid { display: flex; flex-direction: column; gap: 14px; }
.form-item { display: flex; flex-direction: column; gap: 5px; }
.form-item label { font-size: 12px; color: #64748b; font-weight: bold; }
.form-item input { padding: 9px 12px; border: 1px solid #cbd5e1; border-radius: 4px; outline: none; font-size: 13px; color: #1e293b; background: #fff; }
.form-item input:focus { border-color: #3b82f6; }

.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 30px; border-top: 1px solid #e2e8f0; padding-top: 15px; }
.btn-cancel { padding: 9px 16px; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; cursor: pointer; color: #475569; font-weight: bold; }
.btn-cancel:hover { background: #f1f5f9; }
.btn-save { padding: 9px 16px; border: none; background: #3b82f6; color: white; border-radius: 4px; cursor: pointer; font-weight: bold; }
.btn-save:hover { background: #2563eb; }
</style>