import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: Dashboard },
  // 👇 新增这一行，指向即将创建的大屏文件
  { path: '/bigscreen', component: () => import('../views/BigScreen.vue') },
  { path: '/history', component: () => import('../views/History.vue') },
  { path: '/settings', component: () => import('../views/Settings.vue') },
  { path: '/cleanup', component: () => import('../views/Cleanup.vue') }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router