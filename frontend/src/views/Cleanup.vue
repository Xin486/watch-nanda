<template>
  <div class="cleanup-page">
    <div class="panel">
      <h2>🗑️ 数据清理与节点管理</h2>
      <p class="desc">您可以在此页面管理服务器节点及其产生的历史负载数据。红色按钮为高危操作，请谨慎点击。</p>
      
      <table class="data-table">
        <thead>
          <tr>
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
            <td>{{ server.id }}</td>
            <td><strong>{{ server.hostname }}</strong></td>
            <td><span class="ip-text">{{ server.ip_address }}</span></td>
            <td><span class="group-tag">{{ server.group_name || '未分组' }}</span></td>
            <td>
              <span :class="server.status === 'online' ? 'text-green' : 'text-red'">
                ● {{ server.status === 'online' ? '在线' : '离线' }}
              </span>
            </td>
            <td class="actions">
              <button class="btn-view" @click="$router.push('/history')">📈 查历史数据</button>
              <button class="btn-clean" @click="cleanHistory(server)">🧹 清理历史数据</button>
              <button class="btn-delete" @click="deleteServer(server)">❌ 删除服务器</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
// 数据清理页：历史数据清理与节点删除
import { onMounted } from 'vue'
import { api } from '../api'
import { useServers } from '../composables/useServers'

const { servers, subscribe } = useServers()

// 🧹 清理该节点的所有折线图历史数据
const cleanHistory = async (server) => {
  const isConfirm = window.confirm(`【清理确认】\n\n确定要清空 [ ${server.hostname} ] 的所有历史负载数据吗？\n当前服务器的配置状态会被保留，但过去的折线图数据将被全部清零。`)
  if (!isConfirm) return

  try {
    await api.clearHistory(server.id)
    alert('✅ 历史数据已成功清空！')
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
    // SSE 会在下一个推送周期自动刷新列表，无需手动拉取
    window.dispatchEvent(new CustomEvent('server-updated'))
  } catch (error) {
    alert('删除失败，请检查网络或后端日志')
  }
}

onMounted(() => {
  subscribe()
})
</script>

<style scoped>
.cleanup-page { padding: 20px; }
.panel { background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
h2 { margin-top: 0; color: #1e293b; }
.desc { color: #64748b; margin-bottom: 25px; font-size: 14px; }

.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 12px 15px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: middle; }
.data-table th { background-color: #f8fafc; color: #475569; font-weight: bold; }
.data-table tr:hover { background-color: #f1f5f9; }

.ip-text { font-family: monospace; color: #64748b; }
.group-tag { background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
.text-green { color: #10b981; font-weight: bold; }
.text-red { color: #ef4444; font-weight: bold; }

.actions { display: flex; gap: 10px; }
button { padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; font-weight: bold; font-size: 13px; }

/* 查历史按钮 - 蓝色 */
.btn-view { background: #eff6ff; border: 1px solid #bfdbfe; color: #2563eb; }
.btn-view:hover { background: #3b82f6; color: #fff; border-color: #3b82f6; }

/* 清理历史按钮 - 橙色 */
.btn-clean { background: #fff7ed; border: 1px solid #fed7aa; color: #ea580c; }
.btn-clean:hover { background: #f97316; color: #fff; border-color: #f97316; }

/* 彻底删除按钮 - 红色 */
.btn-delete { background: #fef2f2; border: 1px solid #fecaca; color: #dc2626; }
.btn-delete:hover { background: #ef4444; color: #fff; border-color: #ef4444; }
</style>