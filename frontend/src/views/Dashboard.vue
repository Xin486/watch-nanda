<template>
  <div class="dashboard">
    <header class="top-bar">
      <div class="stats">
        <div class="stat-item"><span class="label">总服务器</span><span class="value">{{ servers.length }}</span></div>
        <div class="stat-item"><span class="label">在线</span><span class="value text-green">{{ servers.filter(s => s.status === 'online').length }}</span></div>
        <div class="stat-item"><span class="label">离线</span><span class="value text-red">{{ servers.filter(s => s.status === 'offline').length }}</span></div>
      </div>
      <div class="time-display">🕒 {{ currentTime }}</div>
      <div class="filters">
        <input type="text" placeholder="搜索服务器..." class="search-input" v-model="searchQuery">
      </div>
    </header>

    <div class="server-grid">
      <div class="server-card clickable-card" v-for="server in filteredServers" :key="server.id" :class="server.status" @click="openEditModal(server)">
        <div class="card-header">
          <div class="server-info">
            <strong>{{ server.hostname }}</strong>
            <span class="ip">{{ server.ip_address }}</span>
            <span class="time">更新: {{ formatTime(server.timestamp) }}</span>
          </div>
          <div class="status-badge" :class="server.status">{{ server.status === 'online' ? '在线' : '离线' }}</div>
        </div>

        <div class="card-body" v-if="server.status === 'online'">
          <div class="rings-container">
            <RingChart :percent="server.cpu_percent" label="CPU" :subText="`${server.cpu_cores}核`" />
            <RingChart :percent="server.ram_percent" label="内存" :subText="`${Math.round(server.ram_used_mb/1024)}/${Math.round(server.ram_total_mb/1024)}G`" />
            <RingChart :percent="getGpuAvg(server.gpu_data)" label="GPU" :subText="`${server.gpu_data ? server.gpu_data.length : 0}张`" />
          </div>
          <div class="detail-info">
            <div class="info-row top-process" v-if="server.top_process"><span class="icon">🔥</span><span class="text">{{ server.top_process }}</span></div>
            <div class="gpu-list" v-if="server.gpu_data && server.gpu_data.length > 0">
              <div class="info-row" v-for="(gpu, index) in server.gpu_data.slice(0, 2)" :key="index">
  <svg class="icon-svg" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
    <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>
    <rect x="9" y="9" width="6" height="6"></rect>
    <line x1="9" y1="1" x2="9" y2="4"></line>
    <line x1="15" y1="1" x2="15" y2="4"></line>
    <line x1="9" y1="20" x2="9" y2="23"></line>
    <line x1="15" y1="20" x2="15" y2="23"></line>
    <line x1="20" y1="9" x2="23" y2="9"></line>
    <line x1="20" y1="14" x2="23" y2="14"></line>
    <line x1="1" y1="9" x2="4" y2="9"></line>
    <line x1="1" y1="14" x2="4" y2="14"></line>
  </svg>
  <span class="text">GPU {{ index }}: {{ gpu.memory_used }} / {{ gpu.memory_total }}</span>
</div>
              <div class="more-gpu" v-if="server.gpu_data.length > 2">还有 {{ server.gpu_data.length - 2 }} 张显卡...点击查看详情</div>
            </div>
          </div>
        </div>
        <div class="card-body offline-body" v-else>无法连接 / 离线</div>
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
  <svg class="icon-svg" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
    <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>
    <rect x="9" y="9" width="6" height="6"></rect>
    <line x1="9" y1="1" x2="9" y2="4"></line>
    <line x1="15" y1="1" x2="15" y2="4"></line>
    <line x1="9" y1="20" x2="9" y2="23"></line>
    <line x1="15" y1="20" x2="15" y2="23"></line>
    <line x1="20" y1="9" x2="23" y2="9"></line>
    <line x1="20" y1="14" x2="23" y2="14"></line>
    <line x1="1" y1="9" x2="4" y2="9"></line>
    <line x1="1" y1="14" x2="4" y2="14"></line>
  </svg>
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
            
            <div class="modal-offline-state" v-else>
              ⚠️ 节点当前处于离线状态，无法抓取实时监控数据。
            </div>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import RingChart from '../components/RingChart.vue'

const route = useRoute()
const servers = ref([])
const searchQuery = ref('')
const currentTime = ref(new Date().toLocaleTimeString())
let timer = null
let dataTimer = null

const showEditModal = ref(false)
const editForm = ref({})
const availableGroups = ref(JSON.parse(localStorage.getItem('customGroups') || '[]'))

const fetchServers = async () => {
  try {
    const host = window.location.hostname
    const res = await axios.get(`http://${host}:7980/api/servers`)
    servers.value = res.data
    
    const dbGroups = res.data.map(s => s.group_name)
    availableGroups.value = Array.from(new Set([...availableGroups.value, ...dbGroups])).filter(Boolean)
  } catch (error) {
    console.error('获取数据失败', error)
  }
}

const filteredServers = computed(() => {
  let list = servers.value
  if (route.query.group) {
    list = list.filter(s => s.group_name === route.query.group)
  }
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

const formatTime = (isoString) => {
  if (!isoString) return '--:--:--'
  return new Date(isoString).toLocaleTimeString()
}

// 👑 打开弹窗时，把所有的实时监控参数全部打包拷进去
const openEditModal = (server) => {
  editForm.value = { 
    id: server.id, 
    hostname: server.hostname, 
    ip_address: server.ip_address, 
    group_name: server.group_name,
    ssh_user: server.ssh_user || 'root',
    ssh_port: server.ssh_port || 22,
    // 👇 注入实时动态指标
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

const saveServerInfo = async () => {
  try {
    const host = window.location.hostname
    await axios.post(`http://${host}:7980/api/server/${editForm.value.id}/update`, editForm.value)
    showEditModal.value = false
    await fetchServers()
    window.dispatchEvent(new CustomEvent('server-updated')) 
  } catch (error) {
    alert('保存失败，请检查网络或后台服务')
  }
}

onMounted(() => {
  fetchServers()
  dataTimer = setInterval(fetchServers, 60000)
  timer = setInterval(() => { currentTime.value = new Date().toLocaleTimeString() }, 1000)
  window.addEventListener('server-updated', fetchServers)
})

onUnmounted(() => {
  clearInterval(timer)
  clearInterval(dataTimer)
})
</script>

<style scoped>
/* 保持原有基础监控页面的卡片格栅样式 */
.dashboard { padding: 20px; }
.top-bar { display: flex; justify-content: space-between; align-items: center; background: #fff; padding: 15px 25px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 20px; }
.stats { display: flex; gap: 30px; }
.stat-item { display: flex; flex-direction: column; align-items: center; }
.stat-item .label { font-size: 12px; color: #64748b; margin-bottom: 4px; }
.stat-item .value { font-size: 20px; font-weight: bold; color: #1e293b; }
.text-green { color: #10b981 !important; }
.text-red { color: #ef4444 !important; }
.time-display { color: #64748b; font-size: 14px; }
.filters { display: flex; gap: 10px; }
.search-input { padding: 8px 12px; border: 1px solid #e2e8f0; border-radius: 4px; outline: none; width: 200px; }
.server-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 20px; }
.server-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; transition: transform 0.2s, box-shadow 0.2s; }
.clickable-card { cursor: pointer; }
.clickable-card:hover { transform: translateY(-3px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-color: #cbd5e1; }
.server-card.offline { border-color: #fca5a5; background: #fef2f2; }
.card-header { padding: 12px 15px; border-bottom: 1px solid #f1f5f9; display: flex; justify-content: space-between; align-items: center; }
.server-info { display: flex; gap: 10px; align-items: center; font-size: 13px; color: #64748b; }
.server-info strong { color: #1e293b; font-size: 15px; }
.status-badge { font-size: 12px; font-weight: bold; }
.status-badge.online { color: #10b981; }
.status-badge.offline { color: #ef4444; }
.card-body { padding: 20px; display: flex; flex-direction: column; gap: 15px; }
.rings-container { display: flex; justify-content: space-around; width: 100%; }
.detail-info { background: #f8fafc; border-radius: 6px; padding: 10px; font-size: 12px; color: #475569; }
.info-row { display: flex; align-items: center; margin-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.icon { margin-right: 6px; font-size: 14px; }
.top-process { color: #ea580c; font-weight: 500; border-bottom: 1px solid #e2e8f0; padding-bottom: 5px; margin-bottom: 5px; }
.more-gpu { text-align: center; color: #3b82f6; font-size: 11px; margin-top: 5px; font-weight: bold; }
.offline-body { color: #ef4444; font-weight: bold; align-items: center; justify-content: center; height: 100px; flex-direction: row;}

/* 🪐 升级版：大弹窗双栏样式 */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
.detailed-panel { background: #fff; padding: 25px; border-radius: 12px; width: 850px; max-width: 95vw; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }
.modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; }
.modal-header h3 { margin: 0; color: #0f172a; font-size: 18px; }
.modal-close { cursor: pointer; color: #94a3b8; font-size: 18px; }
.modal-close:hover { color: #ef4444; }

.dual-layout { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 30px; margin-top: 20px; }
.pane-title { margin-top: 0; margin-bottom: 15px; color: #334155; border-left: 4px solid #3b82f6; padding-left: 8px; font-size: 14px; }

/* 左侧指标细节 */
.metrics-pane { background: #f8fafc; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; max-height: 500px; overflow-y: auto; }
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

/* GPU 详细卡片 */
.gpu-detailed-section { margin-top: 20px; border-top: 1px solid #e2e8f0; padding-top: 15px; }
.gpu-detail-card { background: #fff; padding: 12px; border-radius: 6px; border: 1px solid #e2e8f0; margin-bottom: 10px; }
.gpu-meta { display: flex; justify-content: space-between; font-size: 12px; font-weight: bold; margin-bottom: 6px; }
.gpu-idx { color: #3b82f6; }
.gpu-model { color: #1e293b; flex: 1; margin-left: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gpu-temp { color: #10b981; }
.gpu-temp.hot { color: #ef4444; animation: blink 1s infinite alternate; }
.gpu-mem-text { font-size: 11px; color: #64748b; margin-bottom: 4px; }
.progress-bar.mini { height: 5px; }
.no-gpu-hint { font-size: 12px; color: #94a3b8; text-align: center; margin-top: 15px; }
.modal-offline-state { height: 200px; display: flex; align-items: center; justify-content: center; color: #ef4444; font-weight: bold; font-size: 14px; }

/* 右侧表单修改 */
.form-pane { display: flex; flex-direction: column; justify-content: space-between; }
.form-grid { display: flex; flex-direction: column; gap: 14px; }
.form-item { display: flex; flex-direction: column; gap: 5px; }
.form-item label { font-size: 12px; color: #64748b; font-weight: bold; }
.form-item input { padding: 9px 12px; border: 1px solid #cbd5e1; border-radius: 4px; outline: none; font-size: 13px; }
.form-item input:focus { border-color: #3b82f6; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 30px; border-top: 1px solid #e2e8f0; padding-top: 15px; }
.btn-cancel { padding: 9px 16px; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; cursor: pointer; color: #475569; font-weight: bold; }
.btn-cancel:hover { background: #f1f5f9; }
.btn-save { padding: 9px 16px; border: none; background: #3b82f6; color: white; border-radius: 4px; cursor: pointer; font-weight: bold; }
.btn-save:hover { background: #2563eb; }

@keyframes blink {
  0% { opacity: 1; }
  100% { opacity: 0.5; }
}
</style>