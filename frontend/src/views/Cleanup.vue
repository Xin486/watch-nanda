<template>
  <div class="cleanup-page">
    <div class="panel">
      <h2>数据清理与节点管理</h2>
      <p class="desc">您可以在此页面管理服务器节点及其产生的历史负载数据。红色按钮为高危操作，请谨慎点击。</p>
      
      <section class="batch-entry" aria-label="批量重新部署 Agent"><div><h3>批量重新部署 Agent</h3><p>自动选择已使用 Agent 的节点，或逐台勾选；进入独立页面后按队列部署，不清理节点或历史数据。</p></div><div class="batch-actions"><button class="btn-view" :disabled="!agents.length" @click="selectAllAgents">自动选择已部署 Agent（{{ agents.length }} 台）</button><button class="btn-view" :disabled="!selectedIds.length" @click="selectedIds = []">清空选择</button><button class="btn-view" :disabled="!selected.length" @click="openBatch">一键批量重部署（已选 {{ selected.length }} 台）↗</button></div></section>
      <table class="data-table">
        <thead>
          <tr>
            <th>批量选择</th>
            <th>ID</th>
            <th>主机名</th>
            <th>IP 地址</th>
            <th>分组</th>
            <th>当前状态</th>
            <th>操作面板</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="server in servers" :key="server.id">
            <td><input v-model="selectedIds" type="checkbox" :value="server.id" :disabled="server.collection_mode !== 'agent'" :aria-label="'选择 '+server.hostname+' 重新部署 Agent'"><small v-if="server.collection_mode !== 'agent'">SSH 节点</small></td>
            <td>{{ server.id }}</td>
            <td><strong>{{ server.hostname }}</strong></td>
            <td><span class="ip-text">{{ server.ip_address }}</span></td>
            <td><span class="group-tag">{{ server.group_name || '未分组' }}</span></td>
            <td>
              <span :class="['node-status', server.status]">
                ● {{ statusLabel(server.status) }}
              </span>
            </td>
            <td class="actions">
              <button class="btn-view" @click="$router.push('/history')">查历史数据</button>
              <button class="btn-clean" @click="cleanHistory(server)">清理历史数据</button>
              <button class="btn-delete" @click="deleteServer(server)">删除服务器</button>
            </td>
          </tr>
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
import { statusLabel } from '../utils/serverStatus'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const { servers, subscribe } = useServers()
const router = useRouter(), selectedIds = ref([])
const agents = computed(() => eligibleAgents(servers.value))
const selected = computed(() => selectedAgents(servers.value, selectedIds.value))
function selectAllAgents() { selectedIds.value = agents.value.map(node => node.id) }
function openBatch() { if (selected.value.length) router.push({ path: '/agents/batch-redeploy', query: { nodes: selected.value.map(node => node.id).join(',') } }) }

// 🧹 清理该节点的所有折线图历史数据
const cleanHistory = async (server) => {
  const isConfirm = window.confirm(`【清理确认】\n\n确定要清空 [ ${server.hostname} ] 的所有历史负载数据吗？\n当前服务器的配置状态会被保留，但过去的折线图数据将被全部清零。`)
  if (!isConfirm) return

  try {
    await api.clearHistory(server.id)
    alert('✅ 历史数据已成功清空！')
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (error) {
    alert('清理失败，请检查网络或后端日志')
  }
}

// ❌ 彻底删除该节点
const deleteServer = async (server) => {
  const isConfirm = window.confirm(`⚠️ 【危险操作提示】\n\n您正在尝试彻底删除节点 [ ${server.hostname} ]！\n\n删除后：\n1. 该服务器将从监控大屏完全消失。\n2. 该服务器名下的所有历史监控数据将一并销毁。\n\n您确定要继续吗？(此操作不可逆)`)
  if (!isConfirm) return

  try {
    await api.deleteServer(server.id)
    alert('✅ 服务器及关联数据已彻底删除！')
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (error) {
    alert('删除失败，请检查网络或后端日志')
  }
}

onMounted(() => {
  subscribe()
})
</script>

<style scoped>
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

.actions { display: flex; gap: 10px; }
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
@media(max-width:700px) { .cleanup-page { padding:20px 16px; } }
</style>
