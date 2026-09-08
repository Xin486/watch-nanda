<template>
  <div class="app-layout">
    <!-- 左侧深色侧边栏 -->
    <aside class="sidebar">
      <div class="logo-area">
        <h2>{{ companyName || '南大仙林' }}</h2>
      </div>
      
      <nav class="menu">
        <router-link to="/dashboard" class="menu-item" :class="{ active: $route.path === '/dashboard' && !$route.query.group }">
          <i class="icon">📊</i> 全部节点
        </router-link>
        
        <div class="menu-group-header">
          <span>服务器分组</span>
          <span class="add-group-btn" @click="addNewGroup" title="新增分组">➕</span>
        </div>
        
        <router-link 
          v-for="group in allGroups" 
          :key="group"
          :to="`/dashboard?group=${group}`" 
          class="menu-item sub-item"
          :class="{ active: $route.query.group === group }"
        >
          <i class="icon">📁</i> {{ group }}
        </router-link>
        
        <div class="menu-group-header" style="margin-top: 15px;">
          <span>管理操作</span>
        </div>
        
        <a href="#" class="menu-item" @click.prevent="openAddServerModal">
          <i class="icon">🖥️</i> 增加服务器
        </a>
        <router-link to="/history" class="menu-item" active-class="active">
          <i class="icon">📈</i> 历史负载
        </router-link>
        <router-link to="/settings" class="menu-item" active-class="active">
          <i class="icon">⚙️</i> 设置
        </router-link>
        <router-link to="/cleanup" class="menu-item" active-class="active">
          <i class="icon">🗑️</i> 数据清理
        </router-link>
      </nav>
    </aside>

    <!-- 右侧主体内容区域 -->
    <main class="main-content">
      <router-view />
    </main>

    <!-- 新增服务器弹窗 -->
    <div class="modal-overlay" v-if="showAddModal" @click.self="showAddModal = false">
      <div class="modal-content">
        <h3>新增监控节点</h3>
        <div class="form-grid">
          <div class="form-item"><label>主机名 (必填)</label><input type="text" v-model="addForm.hostname" placeholder="例如: 姚-ceph-62"></div>
          <div class="form-item"><label>IP 地址 (必填)</label><input type="text" v-model="addForm.ip_address" placeholder="例如: 192.168.1.100"></div>
          <div class="form-item">
            <label>分组名称</label>
            <input type="text" v-model="addForm.group_name" list="add-group-suggestions" placeholder="留空则为未分组">
            <datalist id="add-group-suggestions">
              <option v-for="g in allGroups" :key="g" :value="g"></option>
            </datalist>
          </div>
          <div class="form-item"><label>SSH 用户名</label><input type="text" v-model="addForm.ssh_user"></div>
          <div class="form-item"><label>SSH 端口</label><input type="number" v-model="addForm.ssh_port"></div>
        </div>
        <p class="hint-text">注意：添加前请确保已配置服务器免密 SSH 登录，否则节点将一直显示离线。</p>
        <div class="modal-actions">
          <button class="btn-cancel" @click="showAddModal = false">取消</button>
          <button class="btn-save" @click="submitAddServer">确认添加</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 全局外壳组件：左侧导航栏 + 路由出口 + 新增服务器弹窗
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
import { useServers } from './composables/useServers'

const route = useRoute()
const { servers, subscribe } = useServers()
const companyName = ref(localStorage.getItem('companyName') || '南大仙林')
const customGroups = ref(JSON.parse(localStorage.getItem('customGroups') || '[]'))

// 弹窗状态与表单
const showAddModal = ref(false)
const addForm = ref({ hostname: '', ip_address: '', group_name: '', ssh_user: 'root', ssh_port: 22 })

// 分组直接从 SSE 推送的 servers 数据实时派生，无需单独请求
const allGroups = computed(() => {
  const dbGroups = [...new Set(servers.value.map(s => s.group_name))]
  const set = new Set([...dbGroups, ...customGroups.value])
  return Array.from(set).filter(g => g && g.trim() !== '')
})

const addNewGroup = () => {
  const name = prompt('请输入新分组名称：\n(新增后在编辑/添加服务器时填入此名称即可归入该组)')
  if (name && name.trim() && !allGroups.value.includes(name.trim())) {
    customGroups.value.push(name.trim())
    localStorage.setItem('customGroups', JSON.stringify(customGroups.value))
  }
}

const openAddServerModal = () => {
  addForm.value = { hostname: '', ip_address: '', group_name: '', ssh_user: 'root', ssh_port: 22 }
  showAddModal.value = true
}

const submitAddServer = async () => {
  if (!addForm.value.hostname || !addForm.value.ip_address) {
    return alert('主机名和 IP 地址不能为空！')
  }
  try {
    await api.addServer(addForm.value)
    alert('服务器添加成功！后台将在一分钟内尝试连接。')
    showAddModal.value = false
    window.dispatchEvent(new CustomEvent('servers-refresh'))
  } catch (error) {
    alert('添加失败，请检查网络')
  }
}

// 同步浏览器标签页标题
const updatePageTitle = () => {
  document.title = localStorage.getItem('pageTitle') || 'Node Monitor'
}

onMounted(() => {
  subscribe()   // 建立 SSE 连接，侧边栏分组随数据实时更新
  updatePageTitle()

  window.addEventListener('storage', () => {
    companyName.value = localStorage.getItem('companyName') || '南大仙林'
    updatePageTitle()
  })
})

// 监听路由跳转，防止某些浏览器在跳转时自动重置标题
watch(() => route.path, () => {
  updatePageTitle()
})
</script>

<style>
/* 包含之前的全局与侧边栏样式 */
html, body { margin: 0; padding: 0; height: 100%; background: #f1f5f9; font-family: system-ui, -apple-system, sans-serif; }
.app-layout { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 240px; background: #0f172a; color: #cbd5e1; display: flex; flex-direction: column; }
.logo-area { padding: 20px; text-align: center; border-bottom: 1px solid #1e293b; }
.logo-area h2 { margin: 0; color: #fff; font-size: 20px; font-weight: 600; letter-spacing: 1px; }
.menu { flex: 1; overflow-y: auto; padding: 20px 0; }
.menu-group-header { display: flex; justify-content: space-between; align-items: center; padding: 15px 20px 5px; font-size: 12px; color: #64748b; font-weight: bold; }
.add-group-btn { cursor: pointer; color: #3b82f6; font-size: 14px; transition: transform 0.2s; }
.add-group-btn:hover { color: #60a5fa; transform: scale(1.2); }
.menu-item { display: flex; align-items: center; padding: 12px 20px; color: #cbd5e1; text-decoration: none; transition: 0.2s; }
.menu-item:hover { background: #1e293b; color: #fff; }
.menu-item.active { background: #3b82f6; color: #fff; border-left: 4px solid #93c5fd; padding-left: 16px;}
.icon { margin-right: 12px; font-style: normal; font-size: 18px; }
.sub-item { padding-left: 30px; font-size: 14px; }
.main-content { flex: 1; overflow-y: auto; background: #f8fafc; }

/* 弹窗样式 */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
.modal-content { background: #fff; padding: 25px; border-radius: 8px; width: 400px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }
.modal-content h3 { margin-top: 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; color: #1e293b; }
.form-grid { display: flex; flex-direction: column; gap: 12px; margin: 20px 0; }
.form-item { display: flex; flex-direction: column; gap: 5px; }
.form-item label { font-size: 13px; color: #64748b; font-weight: bold; }
.form-item input { padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 4px; outline: none; transition: border-color 0.2s; }
.form-item input:focus { border-color: #3b82f6; }
.hint-text { font-size: 12px; color: #ef4444; margin-bottom: 15px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }
.btn-cancel { padding: 8px 16px; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; cursor: pointer; color: #475569; }
.btn-save { padding: 8px 16px; border: none; background: #3b82f6; color: white; border-radius: 4px; cursor: pointer; font-weight: bold; }
.btn-save:hover { background: #2563eb; }
</style>