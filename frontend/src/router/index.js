/**
 * 路由表
 * 首页 Dashboard 静态导入（首屏直达）；
 * 大屏 / 历史 / 设置 / 清理等页面懒加载（跳转时才下载对应代码）。
 */
import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: Dashboard },
  { path: '/bigscreen', component: () => import('../views/BigScreen.vue') },
  { path: '/history', component: () => import('../views/History.vue') },
  { path: '/settings', component: () => import('../views/Settings.vue') },
  { path: '/settings/ssh-credentials', component: () => import('../views/SshCredentials.vue') },
  { path: '/agents/batch-redeploy', component: () => import('../views/AgentBatchDeploy.vue') },
  { path: '/cleanup', component: () => import('../views/Cleanup.vue') },
  { path: '/nodes/:id/operations', component: () => import('../views/NodeOperations.vue') },
  { path: '/operations', component: () => import('../views/NodeOperations.vue') },
  { path: '/servers/new/:id/agent', component: () => import('../views/AgentDeploy.vue') },
  { path: '/nodes/:id/agent/redeploy', component: () => import('../views/AgentRedeploy.vue') },
  // Older operations bookmarks no longer open the deployment workflow.
  { path: '/nodes/:id/agent', redirect: to => ({ path: `/nodes/${to.params.id}/operations`, query: to.query }) },
  { path: '/agents', redirect: '/operations' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
