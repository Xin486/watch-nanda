/**
 * 应用入口：创建 Vue 实例并挂载路由
 */
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(router)      // 注册 vue-router
app.mount('#app')    // 挂载到 index.html 中的 #app
