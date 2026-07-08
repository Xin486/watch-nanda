<template>
  <div class="cleanup-page">
    <div class="panel">
      <h2>服务器数据管理</h2>
      <p class="desc">您可以单独清理某台服务器的历史记录，以释放 MySQL 数据库空间。</p>
      
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>主机名</th>
            <th>IP 地址</th>
            <th>分组</th>
            <th>在线状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="server in servers" :key="server.id">
            <td>{{ server.id }}</td>
            <td><strong>{{ server.hostname }}</strong></td>
            <td>{{ server.ip_address }}</td>
            <td><span class="group-tag">{{ server.group_name }}</span></td>
            <td>
              <span :class="server.status === 'online' ? 'text-green' : 'text-red'">
                ● {{ server.status === 'online' ? '在线' : '离线' }}
              </span>
            </td>
            <td class="actions">
              <button class="btn-check" @click="checkStats(server)">查记录数</button>
              <button class="btn-delete" @click="clearStats(server)">清空历史数据</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const servers = ref([])

const fetchServers = async () => {
  try {
    const host = window.location.hostname
    const res = await axios.get(`http://${host}:7980/api/servers`)
    servers.value = res.data
  } catch (error) {
    console.error('获取列表失败', error)
  }
}

// 检查该服务器在数据库中有多少条记录
const checkStats = async (server) => {
  try {
    const host = window.location.hostname
    const res = await axios.get(`http://${host}:7980/api/server/${server.id}/stats_count`)
    alert(`节点 [${server.hostname}] 当前在数据库中共存有 ${res.data.count} 条历史负载记录。`)
  } catch (error) {
    alert('查询失败')
  }
}

// 清空该服务器历史记录
const clearStats = async (server) => {
  if (confirm(`⚠️ 危险操作！\n\n确定要清空节点 [${server.hostname}] 的所有历史负载数据吗？这不会删除服务器本身，但折线图数据将丢失！`)) {
    try {
      const host = window.location.hostname
      await axios.post(`http://${host}:7980/api/server/${server.id}/clear_stats`)
      alert('清空成功！')
    } catch (error) {
      alert('清空失败')
    }
  }
}

onMounted(() => {
  fetchServers()
})
</script>

<style scoped>
.cleanup-page { padding: 20px; }
.panel { background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
h2 { margin-top: 0; color: #1e293b; }
.desc { color: #64748b; margin-bottom: 20px; font-size: 14px; }

.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 12px 15px; border-bottom: 1px solid #e2e8f0; text-align: left; }
.data-table th { background-color: #f8fafc; color: #475569; font-weight: bold; }
.data-table tr:hover { background-color: #f1f5f9; }

.group-tag { background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.text-green { color: #10b981; font-weight: bold; }
.text-red { color: #ef4444; font-weight: bold; }

.actions { display: flex; gap: 8px; }
.btn-check { background: #f8fafc; border: 1px solid #cbd5e1; color: #475569; padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; }
.btn-check:hover { background: #e2e8f0; }
.btn-delete { background: #fef2f2; border: 1px solid #fca5a5; color: #ef4444; padding: 6px 12px; border-radius: 4px; cursor: pointer; transition: 0.2s; }
.btn-delete:hover { background: #ef4444; color: #fff; }
</style>
