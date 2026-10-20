<template>
  <div class="cleanup-page">
    <div class="panel">
      <h2>数据清理与节点管理</h2>
      <p class="desc">您可以在此页面管理服务器节点及其产生的历史负载数据。红色按钮为高危操作，请谨慎点击。</p>
      
      <section class="batch-entry" aria-label="批量重新部署 Agent"><div><h3>批量重新部署 Agent</h3><p>自动选择已使用 Agent 的节点，或逐台勾选；进入独立页面后按队列部署，不清理节点或历史数据。</p></div><div class="batch-actions"><button class="btn-view" :disabled="!agents.length" @click="selectAllAgents">自动选择已部署 Agent（{{ agents.length }} 台）</button><button class="btn-view" :disabled="!selectedIds.length" @click="selectedIds = []">清空选择</button><button class="btn-view" :disabled="!selected.length" @click="openBatch">一键批量重部署（已选 {{ selected.length }} 台）↗</button></div></section>
      <section class="node-filters" aria-label="查找重复服务器">
        <label class="search-field">查找节点
          <input v-model="search" type="search" placeholder="输入名称、节点 ID、IP、端口、分组或采集方式">
        </label>
        <label class="duplicate-filter"><input v-model="duplicatesOnly" type="checkbox">仅看重名节点（{{ inventory.duplicateGroups }} 组 / {{ inventory.duplicateNodes }} 台）</label>
        <button class="btn-view" @click="search = ''; duplicatesOnly = false">重置筛选</button>
        <p class="filter-summary" aria-live="polite">显示 {{ visibleServers.length }} / {{ servers.length }} 台；重名节点优先并排在一起。名称相同不代表同一台机器，请核对 ID、连接地址和采集方式后逐台删除。</p>
      </section>
      <table class="data-table">
        <thead>
          <tr>
            <th>重部署选择</th>
            <th>ID</th>
            <th>主机名</th>
            <th>连接地址（IP:端口）</th>
            <th>采集方式</th>
            <th>分组</th>
            <th>当前状态</th>
            <th>最近成功采集</th>
            <th>操作面板</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="server in visibleServers" :key="server.id" :class="{ 'duplicate-row': server.duplicateCount > 1 }">
            <td><input v-model="selectedIds" type="checkbox" :value="server.id" :disabled="server.collection_mode !== 'agent'" :aria-label="'选择 '+server.hostname+' 重新部署 Agent'"><small v-if="server.collection_mode !== 'agent'">SSH 节点</small></td>
            <td>{{ server.id }}</td>
            <td><strong>{{ server.hostname }}</strong><span v-if="server.duplicateCount > 1" class="duplicate-badge">同名 {{ server.duplicateCount }} 台</span></td>
            <td><span class="ip-text">{{ connectionAddress(server) }}</span></td>
            <td>{{ collectionLabel(server) }}</td>
            <td><span class="group-tag">{{ server.group_name || '未分组' }}</span></td>
            <td>
              <span :class="['node-status', server.status]">
                ● {{ statusLabel(server.status) }}
              </span>
            </td>
            <td class="last-collected">{{ lastCollected(server.last_online) }}</td>
            <td class="actions">
              <button class="btn-view" @click="$router.push('/history')">查历史数据</button>
              <button class="btn-clean" :disabled="pendingId !== null" @click="cleanHistory(server)">清理历史数据</button>
              <button class="btn-delete" :disabled="pendingId !== null" :aria-label="'删除节点 ID '+server.id+' '+server.hostname" @click="deleteServer(server)">删除服务器</button>
            </td>
          </tr>
          <tr v-if="!visibleServers.length"><td colspan="9" class="empty-state">暂无匹配节点，请调整筛选；首次加载请稍候。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
// 数据清理页：历史数据清理与节点删除
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { eligibleAgents, selectedAgents } from '../utils/batchSelection'
import { cleanupInventory, collectionLabel, connectionAddress, filterCleanupNodes, serverIdentity } from '../utils/cleanupNodes'
import { statusLabel } from '../utils/serverStatus'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const { servers, subscribe } = useServers()
const router = useRouter(), selectedIds = ref([])
const search = ref(''), duplicatesOnly = ref(false), pendingId = ref(null)
const inventory = computed(() => cleanupInventory(servers.value))
const visibleServers = computed(() => filterCleanupNodes(inventory.value.rows, search.value, duplicatesOnly.value))
const agents = computed(() => eligibleAgents(servers.value))
const selected = computed(() => selectedAgents(servers.value, selectedIds.value))
function selectAllAgents() { selectedIds.value = agents.value.map(node => node.id) }
function openBatch() { if (selected.value.length) router.push({ path: '/agents/batch-redeploy', query: { nodes: selected.value.map(node => node.id).join(',') } }) }
function lastCollected(value) {
  if (!value) return '暂无成功采集'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { hour12: false })
}

// 🧹 清理该节点的所有折线图历史数据
const cleanHistory = async (server) => {
  if (pendingId.value !== null) return
  const isConfirm = window.confirm(`【清理确认】\n\n${serverIdentity(server)}\n\n确定清空此节点的所有历史负载数据吗？节点配置会保留，历史数据不可恢复。`)
  if (!isConfirm) return

  pendingId.value = server.id
  try {
    await api.clearHistory(server.id)
    alert('✅ 历史数据已成功清空！')
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (error) {
    alert(error.response?.data?.detail || '清理失败，请检查网络或后端日志')
  } finally {
    pendingId.value = null
  }
}

// ❌ 彻底删除该节点
const deleteServer = async (server) => {
  if (pendingId.value !== null) return
  const isConfirm = window.confirm(`⚠️ 【删除节点确认】\n\n${serverIdentity(server)}\n\n仅删除以上 ID 对应的节点，其全部历史数据和平台 Agent 凭据会一并删除，不会合并到其他同名节点。\n此操作不可逆，请确认保留哪一条后再继续。`)
  if (!isConfirm) return

  pendingId.value = server.id
  try {
    const { data } = await api.deleteServer(server.id)
    if (!data.success) {
      alert(data.message || '删除失败，节点可能已被删除，请刷新后核对')
      window.dispatchEvent(new CustomEvent('servers-refresh'))
      return
    }
    alert('✅ 服务器及关联数据已彻底删除！')
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (error) {
    alert(error.response?.data?.detail || '删除失败，请检查网络或后端日志')
  } finally {
    pendingId.value = null
  }
}

onMounted(() => {
  subscribe()
})
</script>

<style scoped>
.node-filters{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:22px 0}.search-field{display:flex;flex:1;min-width:260px;flex-direction:column;gap:8px;font-size:13px;color:var(--text-secondary)}.search-field input{padding:10px 12px;border:1px solid var(--border);border-radius:6px;background:var(--input-bg,var(--surface));color:var(--text);font:inherit}.search-field input:focus-visible{outline:3px solid var(--focus);outline-offset:2px}.duplicate-filter{display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer}.duplicate-filter input{accent-color:var(--accent)}.filter-summary{width:100%;margin:0;color:var(--text-muted);font-size:12px;line-height:1.8}.duplicate-badge{display:block;width:max-content;margin-top:6px;padding:2px 6px;border:1px solid var(--warning-border);border-radius:4px;background:var(--warning-bg);color:var(--warning);font-size:11px}.duplicate-row td:first-child{border-left:3px solid var(--warning)}.last-collected{white-space:nowrap;color:var(--text-muted);font-size:12px}.empty-state{text-align:center!important;padding:30px!important;color:var(--text-muted)}
.batch-entry{padding:18px;border:1px solid var(--accent-border);background:var(--accent-soft);border-radius:10px;margin:20px 0}.batch-entry h3{font-size:15px;margin:0}.batch-entry p{color:var(--text-muted);font-size:12px;line-height:1.8}.batch-actions{display:flex;gap:10px;flex-wrap:wrap}.batch-actions button:disabled{opacity:.5;cursor:not-allowed}.data-table input[type=checkbox]{accent-color:var(--accent)}.data-table small{display:block;font-size:10px;color:var(--text-muted)}
.cleanup-page { padding: 30px 32px; color: var(--text); }
.panel { overflow-x:auto; background:var(--surface); padding: 25px; border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow); }
h2 { margin-top: 0; color: var(--text); }
.desc { color: var(--text-muted); margin-bottom: 25px; font-size: 14px; }

.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 12px 15px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
.data-table th { background-color: var(--surface-soft); color: var(--text-secondary); font-weight: bold; }
.data-table tr:hover { background-color: var(--surface-muted); }

.ip-text { font-family: monospace; color: var(--text-muted); }
.group-tag { background: var(--accent-soft); color: var(--accent); padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
.text-green { color: var(--success); font-weight: bold; }
.text-red { color: var(--danger); font-weight: bold; }

.actions { display: flex; gap: 10px; flex-wrap:wrap; min-width:190px; }
button { padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; font-weight: bold; font-size: 13px; }

/* 查历史按钮 - 蓝色 */
.btn-view { background: var(--accent-soft); border: 1px solid var(--accent-border); color: var(--accent); }
.btn-view:hover { background:var(--primary-bg); color:var(--on-primary); border-color:var(--primary-bg); }

/* 清理历史按钮 - 橙色 */
.btn-clean { background: var(--warning-bg); border: 1px solid var(--warning-border); color: var(--warning); }
.btn-clean:hover { background: var(--warning-bg); color:var(--warning); border-color: var(--warning); }

/* 彻底删除按钮 - 红色 */
.btn-delete { background: var(--danger-bg); border: 1px solid var(--danger-border); color: var(--danger); }
.btn-delete:hover { background: var(--danger-bg); color:var(--danger); border-color: var(--danger); }
button:focus-visible { outline:3px solid var(--focus); outline-offset:3px; }
button:disabled{opacity:.5;cursor:not-allowed}
@media(max-width:700px) { .cleanup-page { padding:20px 16px; } }
</style>
