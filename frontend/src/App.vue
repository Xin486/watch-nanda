<template>
  <div class="app-layout">
    <!-- 与主内容共享日夜主题的导航栏 -->
    <aside class="sidebar">
      <div class="logo-area">
        <h2>{{ companyName || '南大仙林' }}</h2>
        <ThemeToggle v-if="route.path !== '/dashboard' && route.path !== '/bigscreen'"/>
      </div>
      
      <nav class="menu" aria-label="主导航">
        <router-link to="/dashboard" class="menu-item" :class="{ active: $route.path === '/dashboard' && !$route.query.group }">
          <NavIcon class="icon" name="nodes"/> 全部节点
        </router-link>
        
        <div class="menu-group-header">
          <span>服务器分组</span>
          <button type="button" class="add-group-btn" @click="addNewGroup" aria-label="新增分组" title="新增分组"><NavIcon name="plus"/></button>
        </div>
        
        <router-link 
          v-for="group in allGroups" 
          :key="group"
          :to="`/dashboard?group=${group}`" 
          class="menu-item sub-item"
          :class="{ active: $route.query.group === group }"
        >
          <NavIcon class="icon" name="folder"/> {{ group }}
        </router-link>
        
        <div class="menu-group-header" style="margin-top: 15px;">
          <span>管理操作</span>
        </div>
        
        <a href="#" class="menu-item" @click.prevent="openAddServerModal">
          <NavIcon class="icon" name="add"/> 增加服务器
        </a>
        <router-link to="/history" class="menu-item" active-class="active">
          <NavIcon class="icon" name="history"/> 历史负载
        </router-link>
        <router-link to="/operations" class="menu-item" :class="{ active: $route.path === '/operations' || $route.path.endsWith('/operations') }"><NavIcon class="icon" name="terminal"/> 运维命令行</router-link>
        <router-link to="/settings" class="menu-item" active-class="active" :class="{ active: $route.path.startsWith('/settings/') }">
          <NavIcon class="icon" name="settings"/> 设置
        </router-link>
        <router-link to="/cleanup" class="menu-item" active-class="active" :class="{ active: $route.path === '/agents/batch-redeploy' }">
          <NavIcon class="icon" name="trash"/> 数据清理
        </router-link>
      </nav>
    </aside>

    <!-- 右侧主体内容区域 -->
    <main class="main-content">
      <router-view />
    </main>

    <AddServerModal v-if="showAddModal" :groups="allGroups" @close="showAddModal = false" @created="onServerCreated" />
    <div v-if="addNotice" class="add-node-success" role="status"><span aria-hidden="true">✓</span>{{ addNotice }}</div>
  </div>
</template>

<script setup>
// 全局外壳组件：左侧导航栏 + 路由出口 + 新增服务器弹窗
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AddServerModal from './components/AddServerModal.vue'
import NavIcon from './components/NavIcon.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import { useServers } from './composables/useServers'

const route = useRoute()
const router = useRouter()
const { servers, subscribe } = useServers()
const companyName = ref(localStorage.getItem('companyName') || '南大仙林')
const customGroups = ref(JSON.parse(localStorage.getItem('customGroups') || '[]'))

// 弹窗状态与表单
const showAddModal = ref(false)
const addNotice = ref('')
let noticeTimer = null
onUnmounted(() => clearTimeout(noticeTimer))

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

const openAddServerModal = () => { showAddModal.value = true }
const onServerCreated = (node) => {
  showAddModal.value = false
  window.dispatchEvent(new CustomEvent('servers-refresh'))
  addNotice.value = node?.collection_mode === 'agent' ? `节点 #${node.server_id} 已添加，请在部署页面完成 Agent 安装。` : '服务器已添加，正在等待首次采集。'
  if (node?.collection_mode === 'agent' && node.server_id) router.push(`/servers/new/${node.server_id}/agent`)
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { addNotice.value = '' }, 5000)
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
html, body { margin: 0; padding: 0; height: 100%; background: var(--page-bg); font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif; }
.app-layout { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 240px; flex-shrink: 0; background: var(--sidebar-bg); color: var(--text-secondary); border-right: 1px solid var(--border); display: flex; flex-direction: column; box-sizing: border-box; }
.logo-area { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 24px 24px; border-bottom: 1px solid var(--border); }
.logo-area h2 { margin: 0; color: var(--text); font-size: 21px; font-weight: 650; letter-spacing: .5px; }
.menu { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 12px 22px; }
.menu-group-header { display: flex; justify-content: space-between; align-items: center; padding: 20px 14px 10px; font-size: 11px; color: var(--text-muted); font-weight: 600; }
.add-group-btn { display: grid; place-items: center; width: 26px; height: 26px; padding: 3px; border: 0; border-radius: 6px; background: transparent; cursor: pointer; color: var(--text-secondary); }
.add-group-btn svg { width: 17px; height: 17px; }
.add-group-btn:hover { background: var(--nav-hover-bg); color: var(--accent); }
.menu-item { display: flex; align-items: center; gap: 12px; padding: 12px 15px; margin: 3px 0; border-radius: 9px; color: var(--text-secondary); font-size: 14px; text-decoration: none; }
.menu-item:hover { background: var(--nav-hover-bg); color: var(--text); }
.menu-item.active { background: var(--nav-active-bg); color: var(--accent); box-shadow: inset 3px 0 var(--accent); font-weight: 600; }
.menu-item .icon { width: 20px; height: 20px; flex-shrink: 0; }
.menu-item:focus-visible,.add-group-btn:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.main-content { flex: 1; min-width: 0; overflow-y: auto; background: var(--page-bg); color: var(--text); }

/* 弹窗样式 */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
.modal-content { background:var(--surface); padding: 25px; border-radius: 8px; width: 400px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }
.modal-content h3 { margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 10px; color: var(--text); }
.form-grid { display: flex; flex-direction: column; gap: 12px; margin: 20px 0; }
.form-item { display: flex; flex-direction: column; gap: 5px; }
.form-item label { font-size: 13px; color: var(--text-muted); font-weight: bold; }
.form-item input { padding: 8px 10px; border: 1px solid var(--border); border-radius: 4px; outline: none; transition: border-color 0.2s; }
.form-item input:focus { border-color:var(--primary-bg); }
.hint-text { font-size: 12px; color: var(--danger); margin-bottom: 15px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }
.btn-cancel { padding: 8px 16px; border: 1px solid var(--border); background:var(--surface); border-radius: 4px; cursor: pointer; color: var(--text-secondary); }
.btn-save { padding: 8px 16px; border: none; background:var(--primary-bg); color:var(--on-primary); border-radius: 4px; cursor: pointer; font-weight: bold; }
.btn-save:hover { background:var(--primary-bg); }
.node-status { font-weight: 600; white-space: nowrap; }
.node-status.online { color: var(--success); }
.node-status.offline { color: var(--danger); }
.node-status.stale { color: var(--warning); }
.node-status.pending { color: var(--text-muted); }
button:disabled { opacity: .6; cursor: not-allowed; }
@media (max-width: 1100px) { .sidebar { width: 190px; flex-shrink: 0; } }
@media (max-width: 700px) { .sidebar { width: 150px; } .logo-area { padding: 20px 12px; gap: 6px; } .logo-area h2 { font-size: 16px; } .menu { padding: 10px 8px; } .menu-item { padding: 11px 10px; gap: 8px; font-size: 12px; } }
@media (max-width: 600px) {
  .app-layout { flex-direction: column; height: 100dvh; }
  .sidebar { width: 100%; border-right: 0; border-bottom: 1px solid var(--border); }
  .logo-area { padding: 12px 16px; text-align: left; }
  .logo-area h2 { font-size: 16px; }
  .menu { display: flex; gap: 6px; overflow-x: auto; padding: 8px 12px; }
  .menu-group-header { display: none; }
  .menu-group-header:has(.add-group-btn) { display: flex; align-items: center; padding: 0 5px; }
  .menu-group-header:has(.add-group-btn)>span:first-child { display: none; }
  .menu-item, .menu-item.sub-item, .menu-item.active { padding: 8px 11px; border: 0; border-radius: 6px; font-size: 11px; white-space: nowrap; flex-shrink: 0; }
  .menu-item .icon { width: 16px; height: 16px; }
  .main-content { min-height: 0; }
}
.add-node-success { position: fixed; top: 24px; left: 50%; transform: translateX(-50%); z-index: 1100; display: flex; align-items: center; gap: 10px; max-width: calc(100vw - 32px); padding: 14px 20px; box-sizing: border-box; border: 1px solid var(--success-border); border-radius: 10px; background: var(--success-bg); color: var(--success); font-size: 13px; box-shadow: 0 8px 28px #0f172a1a; }
.add-node-success span { display: grid; place-items: center; flex-shrink: 0; width: 21px; height: 21px; border-radius: 50%; background: var(--success); color:var(--surface); font-size: 13px; }
</style>
