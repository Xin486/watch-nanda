<template>
  <p v-if="!access.secure" class="management-error" role="alert">请通过 HTTPS 打开此页面。</p>
  <form v-else-if="!access.authenticated" class="management-card operator-login" @submit.prevent="access.login">
    <h2>运维登录</h2><p>使用平台运维口令管理常用凭据和部署队列。</p>
    <label>运维口令<input v-model="access.password" type="password" autocomplete="current-password" required></label>
    <button class="management-primary" :disabled="access.busy">{{ access.busy ? '正在登录…' : '登录运维' }}</button>
  </form>
  <template v-else><div class="management-toolbar"><span>运维已登录</span><button @click="access.logout">退出运维登录</button></div><slot /></template>
  <p v-if="access.error" class="management-error" role="alert">{{ access.error }}</p>
</template>
<script setup>
defineProps({ access: { type: Object, required: true } })
</script>
