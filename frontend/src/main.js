/**
 * 应用入口：创建 Vue 实例并挂载路由
 */
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { ensureHttps } from './utils/transport'

if (ensureHttps()) {
  const app = createApp(App)
  app.use(router)
  app.mount('#app')
}
